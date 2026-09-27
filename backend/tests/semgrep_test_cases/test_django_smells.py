"""Semgrep rule fixtures — Django / Python smells.

This file contains **intentionally bad code**. It is never imported or executed;
Semgrep scans it as text and the `semgrep --test` harness compares the reported
lines against the annotations below. Undefined names (`Order`, `cursor`,
`process`) are fine — the rules are syntactic.

Run it with:

    backend/.venv/bin/semgrep --test --metrics=off \
        --config backend/src/agent/codebase/rules/slowtrace-rules.yml \
        backend/tests/semgrep_test_cases/test_django_smells.py

Three annotation facts that are easy to get wrong (all verified on 1.178.0):

1. A `ruleid:` / `ok:` annotation binds to the **immediately following line**, so
   it must sit directly above the line Semgrep reports — not above the `def`.
2. For `for ...:` and `if ...:` patterns the reported line is the **loop/if
   header**, not the query inside the body.
3. Several rules match the *same* line. Every rule that fires there must be
   listed (comma-separated after `ruleid:`), otherwise `--test` reports the extra
   hit as an "incorrect line" and fails.

One more gotcha: the annotation parser scans **every line**, including this
docstring. Writing a literal hash-prefixed annotation inside prose makes Semgrep
think you referenced a rule that does not exist.

Known limitation deliberately NOT expressed as an `ok` case: Semgrep has no
dataflow analysis, so `prefetch_related()` earlier in the function does **not**
suppress `django-n-plus-one-loop`. A loop body still calling
`order.items.all()` will be flagged even when prefetched — annotating that as
`ok` would make the test fail. The negative case below instead shows the
shape that genuinely does not match.
"""

# ---------------------------------------------------------------------------
# django-n-plus-one-loop
# ---------------------------------------------------------------------------


def get_order_summaries():
    orders = Order.objects.filter(status="active")
    # ruleid: django-n-plus-one-loop, django-n-plus-one-fk-access
    for order in orders:
        items = order.items.all()  # <- one query per iteration
        process(items)


# ---------------------------------------------------------------------------
# django-n-plus-one-fk-access
# ---------------------------------------------------------------------------


def get_user_emails():
    # ruleid: django-select-star
    profiles = UserProfile.objects.all()
    # ruleid: django-n-plus-one-fk-access
    for profile in profiles:
        email = profile.user.email  # <- one query per iteration
    return email


# ---------------------------------------------------------------------------
# django-select-star + django-unbounded-queryset (both fire on this line)
# ---------------------------------------------------------------------------


def list_all_users():
    # ruleid: django-select-star, django-unbounded-queryset
    return User.objects.all()


def get_active_orders():
    # ruleid: django-unbounded-queryset
    return Order.objects.filter(status="active")


# ---------------------------------------------------------------------------
# django-missing-select-related (queryset in the loop head)
# ---------------------------------------------------------------------------


def order_report():
    # ruleid: django-missing-select-related, django-n-plus-one-fk-access
    for order in Order.objects.filter(ready=True):
        print(order.customer.name)


# ---------------------------------------------------------------------------
# python-raw-sql-string-concat / python-raw-sql-fstring
# ---------------------------------------------------------------------------


def unsafe_query(user_id):
    # ruleid: python-raw-sql-string-concat
    cursor.execute("SELECT * FROM users WHERE id = " + user_id)


def also_unsafe(user_id):
    # ruleid: python-raw-sql-fstring
    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")


def also_unsafe_db_handle(user_id):
    # ruleid: python-raw-sql-fstring
    db.execute(f"SELECT * FROM users WHERE id = {user_id}")


# ---------------------------------------------------------------------------
# django-count-instead-of-exists
# ---------------------------------------------------------------------------


def check_user_exists(email):
    # ruleid: django-count-instead-of-exists
    if User.objects.filter(email=email).count() > 0:
        return True


def check_no_orders():
    # ruleid: django-count-instead-of-exists
    if len(Order.objects.filter(status="active")) == 0:
        return True


# ===========================================================================
# Negative cases — none of the below may be reported.
# ===========================================================================


def good_order_summaries():
    # Prefetched, and the loop body reads plain fields rather than a related
    # manager call, so no rule matches. django-missing-select-related cannot
    # fire here either: its pattern needs the queryset built in the loop head.
    orders = Order.objects.prefetch_related("items").filter(status="active")
    totals = []
    # ok: django-n-plus-one-loop, django-n-plus-one-fk-access
    for order in orders:
        totals.append(order.total)
    return totals


def specific_columns():
    # ok: django-select-star, django-unbounded-queryset
    return User.objects.only("name", "email")


def paginated_orders():
    # ok: django-unbounded-queryset
    return Order.objects.filter(status="active")[:100]


def correct_exists_check(email):
    # ok: django-count-instead-of-exists
    if User.objects.filter(email=email).exists():
        return True


def safe_query(user_id):
    # ok: python-raw-sql-string-concat, python-raw-sql-fstring
    cursor.execute("SELECT * FROM users WHERE id = %s", [user_id])
    return cursor.fetchone()


def list_comprehension(orders):
    # ok: django-n-plus-one-loop, django-n-plus-one-fk-access
    return [order.total for order in orders]
