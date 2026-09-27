import { describe, expect, it } from 'vitest'
import { parseSseFrame } from '@/api/codebase'

describe('parseSseFrame', () => {
  it('reads a stage the backend sends as both event name and data field', () => {
    const frame =
      'event: started\ndata: {"stage": "started", "data": {"message": "Cloning repository...", "repo_url": "https://github.com/a/b"}}'
    expect(parseSseFrame(frame)).toEqual({
      stage: 'started',
      data: {
        message: 'Cloning repository...',
        repo_url: 'https://github.com/a/b',
      },
    })
  })

  it('falls back to the event name when the payload has no stage', () => {
    const frame = 'event: progress\ndata: {"data": {"elapsed_seconds": 4}}'
    expect(parseSseFrame(frame)).toEqual({
      stage: 'progress',
      data: { elapsed_seconds: 4 },
    })
  })

  it('parses a data-only frame', () => {
    const frame = 'data: {"stage": "error", "data": {"error": "private"}}'
    expect(parseSseFrame(frame)).toEqual({
      stage: 'error',
      data: { error: 'private' },
    })
  })

  it('joins multi-line data fields before parsing', () => {
    const frame = 'data: {"stage": "progress",\ndata:  "data": {}}'
    expect(parseSseFrame(frame)).toEqual({ stage: 'progress', data: {} })
  })

  it('ignores comments, heartbeats and empty frames', () => {
    expect(parseSseFrame(': keep-alive')).toBeNull()
    expect(parseSseFrame('')).toBeNull()
    expect(parseSseFrame('id: 7')).toBeNull()
  })

  it('ignores unknown stage names instead of surfacing garbage', () => {
    expect(parseSseFrame('event: ping\ndata: {"ok": true}')).toBeNull()
    expect(parseSseFrame('data: {"stage": "file_result", "data": {}}')).toBeNull()
  })

  it('returns null rather than throwing on malformed JSON', () => {
    expect(parseSseFrame('event: complete\ndata: {"stage": "complete", "da')).toBeNull()
  })

  it('tolerates a missing space after the field colon', () => {
    const frame = 'event:complete\ndata:{"stage":"progress","data":{"elapsed_seconds":2}}'
    expect(parseSseFrame(frame)).toEqual({
      stage: 'progress',
      data: { elapsed_seconds: 2 },
    })
  })
})
