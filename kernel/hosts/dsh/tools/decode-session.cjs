// GENERATED-BY: portable host evidence migration
// LAST-EDIT: 2026-11-09
// PURPOSE: Decode concatenated DSH Zstandard frames without pipe capture.
// ORIGIN: generic frame parsing from upstream plan_audit_session_decode.cjs
// CUMULATIVE-DELTA: Reject torn tails and reserved blocks instead of accepting stale evidence.
'use strict';
const fs = require('node:fs');
const zlib = require('node:zlib');

function frames(buf) {
  const result = [];
  let pos = 0;
  const need = n => { if (pos + n > buf.length) throw new Error('不完整的日志尾帧，稍后重读'); };
  while (pos < buf.length) {
    need(4);
    const start = pos, magic = buf.readUInt32LE(pos);
    pos += 4;
    if (magic >= 0x184d2a50 && magic <= 0x184d2a5f) {
      need(4);
      const size = buf.readUInt32LE(pos); pos += 4;
      need(size); pos += size;
      continue;
    }
    if (magic !== 0xfd2fb528) throw new Error('无效的 Zstandard 魔数');
    need(1);
    const descriptor = buf[pos++];
    if (descriptor & 0x18) throw new Error('无效的 Zstandard 保留位');
    const single = (descriptor >> 5) & 1, checksum = (descriptor >> 2) & 1;
    const dictionaryBytes = [0, 1, 2, 4][descriptor & 3];
    const fcs = descriptor >> 6;
    const fcsBytes = fcs === 0 ? single : [0, 2, 4, 8][fcs];
    const headerBytes = (single ? 0 : 1) + dictionaryBytes + fcsBytes;
    need(headerBytes); pos += headerBytes;
    for (;;) {
      need(3);
      const block = buf.readUIntLE(pos, 3); pos += 3;
      const type = (block >> 1) & 3, size = block >> 3;
      if (type === 3) throw new Error('无效的 Zstandard 保留块');
      const stored = type === 1 ? 1 : size;
      need(stored); pos += stored;
      if (block & 1) break;
    }
    if (checksum) { need(4); pos += 4; }
    result.push([start, pos]);
  }
  if (!result.length) throw new Error('日志没有完整数据帧');
  return result;
}

try {
  if (process.argv.length !== 4) throw new Error('用法：node decode-session.cjs <source> <new-output>');
  if (typeof zlib.zstdDecompressSync !== 'function') throw new Error('宿主日志解码需要支持 Zstandard 的 Node.js 24 或更新版本');
  const source = process.argv[2], target = process.argv[3];
  if (!fs.statSync(source).isFile()) throw new Error('日志不是普通文件');
  const buf = fs.readFileSync(source);
  const chunks = frames(buf).map(([start, end]) => zlib.zstdDecompressSync(buf.subarray(start, end)));
  fs.writeFileSync(target, Buffer.concat(chunks), {flag: 'wx'});
} catch (error) {
  process.stderr.write(String(error.message || error) + '\n');
  process.exitCode = 4;
}
