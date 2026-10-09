import type { EventRequest, RuntimeSession } from './types';

export function eventFor(
  session: RuntimeSession,
  event_type: string,
  payload: object,
): EventRequest {
  return {
    event_id: crypto.randomUUID(),
    event_type,
    payload,
    source:
      session.runtime.execution_mode === 'SCHEDULE_CLOCK'
        ? 'MANUAL_CONFIRM'
        : session.runtime.execution_mode,
    expected_state_revision: session.runtime.state_revision,
    base_plan_version: session.runtime.current_plan_version,
  };
}
