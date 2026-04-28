import crypto from 'crypto';

export function sha1(data: Buffer): Buffer {
  return crypto.createHash('sha1').update(data).digest();
}
