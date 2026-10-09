import type { Notice } from '../api/types';

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function notice(value: unknown): value is Notice {
  return (
    object(value) &&
    typeof value.cursor === 'number' &&
    Number.isSafeInteger(value.cursor) &&
    value.cursor > 0 &&
    typeof value.plan_version === 'number' &&
    Number.isSafeInteger(value.plan_version) &&
    value.plan_version >= 0 &&
    typeof value.notification_id === 'string' &&
    typeof value.event_id === 'string' &&
    typeof value.kind === 'string' &&
    typeof value.text === 'string' &&
    object(value.data)
  );
}

export class NotificationInbox {
  cursor = 0;
  private readonly seen = new Set<string>();
  private readonly notices: Notice[] = [];
  static restore(serialized: string | null): NotificationInbox {
    const restored = new NotificationInbox();
    if (!serialized) return restored;
    try {
      const saved: unknown = JSON.parse(serialized);
      if (
        !object(saved) ||
        !Array.isArray(saved.notices) ||
        !saved.notices.length ||
        !saved.notices.every(notice) ||
        typeof saved.cursor !== 'number' ||
        !Number.isSafeInteger(saved.cursor) ||
        saved.cursor < Math.max(...saved.notices.map((item) => item.cursor))
      )
        return restored;
      for (const item of saved.notices) restored.receive(item, item.plan_version);
      restored.cursor = saved.cursor;
      return restored;
    } catch {
      return restored;
    }
  }
  serialize(): string {
    return JSON.stringify({ cursor: this.cursor, notices: this.notices.slice(-200) });
  }
  receive(message: Notice, currentVersion: number): boolean {
    if (message.cursor <= this.cursor) return false;
    this.cursor = Math.max(this.cursor, message.cursor);
    const identity = `${message.notification_id}:${message.event_id}`;
    if (this.seen.has(identity)) return false;
    this.seen.add(identity);
    this.notices.push(message);
    return message.kind === 'OPERATION_COMPLETED' || message.plan_version === currentVersion;
  }
  current(version: number): Notice[] {
    return this.notices
      .filter((n) => n.kind === 'OPERATION_COMPLETED' || n.plan_version === version)
      .slice(-200);
  }
}
