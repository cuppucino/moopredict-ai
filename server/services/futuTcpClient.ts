import net from 'net';
import crypto from 'crypto';
import { logger } from '../core/Logger';

/**
 * Direct TCP Client for Moomoo OpenD
 * Implements the raw binary protocol to avoid WebSocket proxy overhead.
 */
export class FutuTcpClient {
  private socket: net.Socket | null = null;
  private serialNo: number = 0;
  private responseHandlers: Map<number, (res: any) => void> = new Map();
  private protoRoot: any;
  
  public onlogin: ((success: boolean, response: any) => void) | null = null;
  public onpush: ((cmd: number, response: any) => void) | null = null;

  private connID: string = "0";

  private readonly METHOD_MAP: Record<string, { id: number, proto: string }> = {
    "InitConnect":         { id: 1001, proto: "InitConnect" },
    "GetAccList":          { id: 2001, proto: "Trd_GetAccList" },
    "GetFunds":            { id: 2101, proto: "Trd_GetFunds" },
    "GetPositionList":     { id: 2102, proto: "Trd_GetPositionList" },
    "PlaceOrder":          { id: 2201, proto: "Trd_PlaceOrder" },
    "Sub":                 { id: 3001, proto: "Qot_Sub" },
    "RegQotPush":          { id: 3002, proto: "Qot_RegQotPush" },
    "GetSecuritySnapshot": { id: 3005, proto: "Qot_GetSecuritySnapshot" },
    "GetKL":               { id: 3006, proto: "Qot_GetKL" },
    "GetHistoryKL":        { id: 3003, proto: "Qot_GetHistoryKL" }
  };

  constructor(protoRoot: any) {
    this.protoRoot = protoRoot;
    
    // Return a Proxy so we can call methods like client.GetAccList(...)
    return new Proxy(this, {
      get: (target, prop: string) => {
        if (prop in target) return (target as any)[prop];
        
        const mapping = target.METHOD_MAP[prop];
        if (mapping) {
          return (payload: any) => target._sendCmd(mapping.id, payload, mapping.proto);
        }
        return undefined;
      }
    });
  }

  public getConnID(): string {
    return this.connID;
  }

  public async start(host: string, port: number): Promise<void> {
    return new Promise((resolve, reject) => {
      this.socket = new net.Socket();
      
      const onConnect = () => {
        logger.info(`[FutuTcpClient] TCP Connected to ${host}:${port}`);
        this.socket?.removeListener('error', onError);
        resolve();
      };

      const onError = (err: Error) => {
        logger.error(`[FutuTcpClient] Connection failed to ${host}:${port}:`, err.message);
        this.socket?.destroy();
        reject(err);
      };

      this.socket.once('connect', onConnect);
      this.socket.once('error', onError);

      this.socket.connect(port, host);

      let buffer = Buffer.alloc(0);
      this.socket.on('data', (data) => {
      buffer = Buffer.concat([buffer, data]);
      
      while (buffer.length >= 44) {
        // Parse Header
        const flag = buffer.slice(0, 2).toString();
        if (flag !== 'FT') {
          logger.error('[FutuTcpClient] Invalid header flag, closing connection');
          this.socket?.destroy();
          return;
        }

        const protoID = buffer.readUInt32LE(2);
        const protoFmtType = buffer.readUInt8(6); // 0 = Protobuf
        const bodyLen = buffer.readUInt32LE(12);
        const serialNo = buffer.readUInt32LE(8);

        if (buffer.length < 44 + bodyLen) break; // Wait for full body

        const body = buffer.slice(44, 44 + bodyLen);
        buffer = buffer.slice(44 + bodyLen);

        this.handlePacket(protoID, serialNo, body);
      }
    });

    this.socket.on('error', (err) => {
      logger.error('[FutuTcpClient] Socket error:', err.message);
      if (this.onlogin) this.onlogin(false, err);
    });

    this.socket.on('close', () => {
      logger.warn('[FutuTcpClient] Connection closed');
    });
  }

  private handlePacket(protoID: number, serialNo: number, body: Buffer) {
    try {
      // Find the corresponding proto response message
      // Note: Moomoo proto naming convention is usually [ProtoName].Response
      // We need to know which proto matches this protoID. 
      // For simplicity, we assume the caller knows what to expect or we map them.
      
      const handler = this.responseHandlers.get(serialNo);
      if (handler) {
        handler(body);
        this.responseHandlers.delete(serialNo);
        
        // If this was InitConnect, capture the connID
        if (protoID === 1001) {
          const ProtoResponse = this.protoRoot.lookup("InitConnect.Response");
          const decoded = ProtoResponse.decode(body);
          if (decoded.s2c?.connID) {
            this.connID = decoded.s2c.connID.toString();
          }
        }
      } else if (this.onpush) {
        this.onpush(protoID, body);
      }
    } catch (e: any) {
      logger.error(`[FutuTcpClient] Packet handling error for ID ${protoID}:`, e.message);
    }
  }

  public async _sendCmd(protoID: number, payload: any, protoName: string): Promise<any> {
    if (!this.socket) throw new Error('Not connected');

    const serialNo = ++this.serialNo;
    
    // 1. Encode Body
    const ProtoRequest = this.protoRoot.lookup(`${protoName}.Request`);
    const ProtoResponse = this.protoRoot.lookup(`${protoName}.Response`);
    
    if (!ProtoRequest || !ProtoResponse) {
      throw new Error(`Proto definition not found for ${protoName}`);
    }

    const body = ProtoRequest.encode(ProtoRequest.create(payload)).finish();
    const bodyLen = body.length;
    const sha1 = crypto.createHash('sha1').update(body).digest();

    // 2. Build Header (44 bytes)
    const header = Buffer.alloc(44);
    header.write('FT', 0);
    header.writeUInt32LE(protoID, 2);
    header.writeUInt8(0, 6); // Protobuf
    header.writeUInt8(0, 7); // Proto version
    header.writeUInt32LE(serialNo, 8);
    header.writeUInt32LE(bodyLen, 12);
    sha1.copy(header, 16);
    // bytes 36-43 are reserved (0)

    // 3. Send
    this.socket.write(Buffer.concat([header, body]));

    // 4. Wait for response
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.responseHandlers.delete(serialNo);
        reject(new Error(`Timeout waiting for response to ${protoName} (${protoID})`));
      }, 10000);

      this.responseHandlers.set(serialNo, (responseBody) => {
        clearTimeout(timeout);
        try {
          const decoded = ProtoResponse.decode(responseBody);
          resolve(decoded);
        } catch (e) {
          reject(e);
        }
      });
    });
  }

  public stop() {
    this.socket?.destroy();
    this.socket = null;
  }
}
