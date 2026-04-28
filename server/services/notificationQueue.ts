import { v4 as uuidv4 } from "uuid";

interface Notification {
  id: string;
  message: string;
  level: string;
  sent: boolean;
  created_at: Date;
}

let queue: Notification[] = [];

export const notificationQueue = {
  enqueue(message: string, level: string = "info") {
    queue.push({
      id: uuidv4(),
      message,
      level,
      sent: false,
      created_at: new Date()
    });
    // Keep last 100
    if (queue.length > 100) queue.shift();
  },

  getPending() {
    return queue.filter(n => !n.sent);
  },

  markAsSent(id: string) {
    const n = queue.find(x => x.id === id);
    if (n) n.sent = true;
  },

  clear() {
    queue = [];
  }
};
