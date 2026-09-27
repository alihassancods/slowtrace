import type { PlanNode } from '@/types/explain'

interface PlanNodeCardProps {
  node: PlanNode
  depth: number
}

function PlanNodeCard({ node, depth }: PlanNodeCardProps) {
  const indent = depth * 20

  return (
    <div style={{ marginLeft: `${indent}px` }} className="mb-2">
      <div className="rounded-lg border border-slate-700 bg-slate-800 px-4 py-3 text-sm">
        <div className="mb-1 font-semibold text-indigo-300">{node['Node Type']}</div>
        <div className="flex flex-wrap gap-x-6 gap-y-0.5 text-xs text-slate-400 font-mono">
          <span>
            Cost:{' '}
            <span className="text-slate-200">
              {node['Startup Cost'].toFixed(2)}..{node['Total Cost'].toFixed(2)}
            </span>
          </span>
          <span>
            Rows:{' '}
            <span className="text-slate-200">{node['Plan Rows'].toLocaleString()}</span>
          </span>
          {node['Actual Rows'] !== undefined && (
            <span>
              Actual Rows:{' '}
              <span className="text-slate-200">{node['Actual Rows'].toLocaleString()}</span>
            </span>
          )}
          {node['Shared Hit Blocks'] !== undefined && (
            <span>
              Shared Hit:{' '}
              <span className="text-slate-200">{node['Shared Hit Blocks'].toLocaleString()}</span>
            </span>
          )}
          {node['Shared Read Blocks'] !== undefined && (
            <span>
              Shared Read:{' '}
              <span className="text-slate-200">{node['Shared Read Blocks'].toLocaleString()}</span>
            </span>
          )}
        </div>
      </div>
      {node.Plans && node.Plans.length > 0 && (
        <div className="mt-1 border-l border-slate-700 ml-4 pl-0">
          {node.Plans.map((child, i) => (
            <PlanNodeCard key={i} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  )
}

interface PlanTreeProps {
  plan: Record<string, unknown>[] | null
}

export default function PlanTree({ plan }: PlanTreeProps) {
  if (!plan || plan.length === 0) {
    return (
      <div className="rounded-lg border border-slate-700 bg-slate-800 p-6 text-center text-sm text-slate-500">
        EXPLAIN not available
      </div>
    )
  }

  return (
    <div>
      {plan.map((root, i) => {
        const rootPlan = (root['Plan'] ?? root) as PlanNode
        return <PlanNodeCard key={i} node={rootPlan} depth={0} />
      })}
    </div>
  )
}
