import { v4 as uuidv4 } from 'uuid';

export interface Notification {
  id: string;
  message: string;
  level: string;
  timestamp: Date;
  sent: boolean;
}

class NotificationQueue {
  private queue: Notification[] = [];
  private readonly MAX_HISTORY = 100;

  public enqueue(message: string, level: string = 'info'): string {
    const id = uuidv4();
    const notification: Notification = {
      id,
      message,
      level,
      timestamp: new Date(),
      sent: false
    };

    this.queue.push(notification);
    
    if (this.queue.length > this.MAX_HISTORY) {
      this.queue.shift();
    }

    return id;
  }

  public getPending(): Notification[] {
    return this.queue.filter(n => !n.sent);
  }

  public markAsSent(id: string): void {
    const n = this.queue.find(item => item.id === id);
    if (n) n.sent = true;
  }

  public getAll(): Notification[] {
    return [...this.queue];
  }
}

export const notificationQueue = new NotificationQueue();
