import { WebSocketServer } from 'ws';
import net from 'net';
import crypto from 'crypto';

const WSS_PORT = 11112;
const TCP_PORT = 11111;
const TCP_HOST = '127.0.0.1';

const WS_HEAD_LEN = 20;   // ft-v1.0(8) + cmd(4) + section(8)
const TCP_HEAD_LEN = 44;  // FT(2) + protoID(4) + fmtType(1) + ver(1) + serialNo(4) + bodyLen(4) + sha1(20) + reserved(8)

// --- Protocol Translation ---

/**
 * Convert WebSocket frame (moomoo-api) → TCP frame (Futu OpenD)
 * WS:  [sign:8][cmd:4 BE][section:8 BE][body...]
 * TCP: [FT:2][protoID:4 LE][fmtType:1][ver:1][serialNo:4 LE][bodyLen:4 LE][sha1:20][reserved:8][body...]
 */
function ws_to_tcp(ws_buf) {
    const cmd = ws_buf.readUInt32BE(8);
    // section is UInt64 BE; use the low 32 bits as serialNo
    const serial_no = ws_buf.readUInt32BE(16);
    const body = ws_buf.length > WS_HEAD_LEN ? ws_buf.slice(WS_HEAD_LEN) : Buffer.alloc(0);
    const body_sha1 = crypto.createHash('sha1').update(body).digest();

    const tcp_buf = Buffer.alloc(TCP_HEAD_LEN + body.length);
    tcp_buf.write('FT', 0, 2, 'ascii');           // szHeaderFlag
    tcp_buf.writeUInt32LE(cmd, 2);                 // nProtoID (LE)
    tcp_buf.writeUInt8(0, 6);                      // nProtoFmtType = 0 (protobuf)
    tcp_buf.writeUInt8(0, 7);                      // nProtoVer = 0
    tcp_buf.writeUInt32LE(serial_no, 8);           // nSerialNo (LE)
    tcp_buf.writeUInt32LE(body.length, 12);         // nBodyLen (LE)
    body_sha1.copy(tcp_buf, 16);                   // arrBodySHA1
    // bytes 36-43: arrReserved (already 0)
    if (body.length > 0) body.copy(tcp_buf, TCP_HEAD_LEN);

    return { tcp_buf, cmd, serial_no };
}

/**
 * Convert TCP frame (Futu OpenD) → WebSocket frame (moomoo-api)
 * TCP: [FT:2][protoID:4 LE][fmtType:1][ver:1][serialNo:4 LE][bodyLen:4 LE][sha1:20][reserved:8][body...]
 * WS:  [sign:8][cmd:4 BE][section:8 BE][error:4 BE][errmsg:20][body...]
 */
function tcp_to_ws(tcp_buf) {
    const cmd = tcp_buf.readUInt32LE(2);
    const serial_no = tcp_buf.readUInt32LE(8);
    const body_len = tcp_buf.readUInt32LE(12);
    const body = body_len > 0 ? tcp_buf.slice(TCP_HEAD_LEN, TCP_HEAD_LEN + body_len) : Buffer.alloc(0);

    // moomoo-api's unpackBuff expects: sign(8) + cmd(4 BE) + section(8 BE) + error(4 BE) + errmsg(20) + body
    const ws_head_len = 44; // 8+4+8+4+20
    const ws_buf = Buffer.alloc(ws_head_len + body.length);
    ws_buf.write('ft-v1.0', 0, 7, 'ascii');
    ws_buf.writeUInt32BE(cmd, 8);
    // section: write serial_no as low word of UInt64 BE
    ws_buf.writeUInt32BE(0, 12);          // high word
    ws_buf.writeUInt32BE(serial_no, 16);  // low word
    ws_buf.writeInt32BE(0, 20);            // error = 0
    // errmsg: 20 null bytes at offset 24-43 (already 0)
    if (body.length > 0) body.copy(ws_buf, ws_head_len);

    return { ws_buf, cmd, serial_no };
}

// --- TCP Stream Parser ---
// TCP is a stream, so we need to buffer and frame responses by reading bodyLen
class TcpFrameParser {
    constructor(on_frame) {
        this.buffer = Buffer.alloc(0);
        this.on_frame = on_frame;
    }
    
    push(data) {
        this.buffer = Buffer.concat([this.buffer, data]);
        this._try_parse();
    }
    
    _try_parse() {
        while (this.buffer.length >= TCP_HEAD_LEN) {
            // Verify FT header
            if (this.buffer[0] !== 0x46 || this.buffer[1] !== 0x54) { // 'F','T'
                console.error('[Proxy] TCP: Invalid header flag, skipping byte');
                this.buffer = this.buffer.slice(1);
                continue;
            }
            const body_len = this.buffer.readUInt32LE(12);
            const total_len = TCP_HEAD_LEN + body_len;
            if (this.buffer.length < total_len) break; // wait for more data
            
            const frame = this.buffer.slice(0, total_len);
            this.buffer = this.buffer.slice(total_len);
            this.on_frame(frame);
        }
    }
}

// --- WebSocket Server ---

const wss = new WebSocketServer({ port: WSS_PORT });

wss.on('connection', (ws) => {
    console.log('[Proxy] WebSocket client connected');
    let tcpConnected = false;
    let pending_messages = [];
    
    const tcp_parser = new TcpFrameParser((tcp_frame) => {
        try {
            const { ws_buf, cmd, serial_no } = tcp_to_ws(tcp_frame);
            console.log(`[Proxy] TCP→WS | cmd=${cmd} serial=${serial_no} total=${ws_buf.length}`);
            ws.send(ws_buf);
        } catch (err) {
            console.error('[Proxy] TCP→WS translation error:', err.message);
        }
    });
    
    const tcpClient = net.createConnection({ port: TCP_PORT, host: TCP_HOST }, () => {
        console.log('[Proxy] Connected to Moomoo OpenD TCP');
        tcpConnected = true;
        // Flush any queued messages
        for (const msg of pending_messages) {
            tcpClient.write(msg);
        }
        pending_messages = [];
    });

    ws.on('message', (data) => {
        const buf = Buffer.from(data);
        
        if (buf.length < 12) return;
        const cmd_be = buf.readUInt32BE(8);
        
        if (cmd_be === 1) {
            // Intercept InitWebSocket (cmd=1) — TCP doesn't have this
            console.log('[Proxy] ★ Intercepting InitWebSocket (cmd=1)');
            const section_bytes = buf.slice(12, 20);
            
            // Protobuf: InitWebSocket.Response { retType: 0, s2c: { connID: 1 } }
            const proto_body = Buffer.from([0x08, 0x00, 0x22, 0x02, 0x10, 0x01]);
            const resp = Buffer.alloc(44 + proto_body.length);
            resp.write('ft-v1.0', 0, 7, 'ascii');
            resp.writeUInt32BE(1, 8);
            section_bytes.copy(resp, 12);
            resp.writeInt32BE(0, 20);
            proto_body.copy(resp, 44);
            
            ws.send(resp);
            return;
        }

        // Translate WS → TCP and forward
        try {
            const { tcp_buf, cmd, serial_no } = ws_to_tcp(buf);
            console.log(`[Proxy] WS→TCP | cmd=${cmd} serial=${serial_no} bodyLen=${tcp_buf.length - TCP_HEAD_LEN}`);
            
            if (tcpConnected) {
                tcpClient.write(tcp_buf);
            } else {
                pending_messages.push(tcp_buf);
            }
        } catch (err) {
            console.error('[Proxy] WS→TCP translation error:', err.message);
        }
    });

    tcpClient.on('data', (data) => {
        tcp_parser.push(data);
    });

    ws.on('close', () => {
        console.log('[Proxy] WebSocket client disconnected');
        tcpClient.end();
    });

    tcpClient.on('error', (err) => {
        console.error('[Proxy] TCP Error:', err.message);
        ws.close();
    });

    ws.on('error', (err) => {
        console.error('[Proxy] WS Error:', err.message);
        tcpClient.destroy();
    });
});

console.log(`[Proxy] WebSocket↔TCP protocol translator v4 on ws://localhost:${WSS_PORT} ↔ tcp://${TCP_HOST}:${TCP_PORT}`);
