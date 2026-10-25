// Print a dsh session log (`session.v4.jsonl.zstd`) as plain JSONL: node harness/dsh/read_session.mjs <path>
//
// dsh appends one Zstandard frame per flush, and Node's one-shot zstdDecompressSync decodes only the first frame of
// a buffer, so this walks the frames by their headers (RFC 8878: magic, frame header, blocks, optional checksum)
// and decodes each alone. A torn last frame (a flush in progress) is left out. Skippable frames are skipped. The
// `dsh` dialect runs this to read the agent's last message at a stop, which dsh's Stop payload does not carry.
import { readFileSync } from 'node:fs'
import { zstdDecompressSync } from 'node:zlib'

const buf = readFileSync(process.argv[2])
let off = 0
while (off + 8 <= buf.length) {
  const magic = buf.readUInt32LE(off)
  if (magic >= 0x184d2a50 && magic <= 0x184d2a5f) { off += 8 + buf.readUInt32LE(off + 4); continue }
  if (magic !== 0xfd2fb528) break
  let p = off + 4
  const fhd = buf[p++]
  const single = (fhd >> 5) & 1
  if (!single) p += 1
  p += [0, 1, 2, 4][fhd & 3] + [single, 2, 4, 8][fhd >> 6]
  let last = 0
  while (!last && p + 3 <= buf.length) {
    const header = buf[p] | (buf[p + 1] << 8) | (buf[p + 2] << 16)
    last = header & 1
    p += 3 + (((header >> 1) & 3) === 1 ? 1 : header >>> 3)
  }
  if (!last) break
  if ((fhd >> 2) & 1) p += 4
  if (p > buf.length) break
  process.stdout.write(zstdDecompressSync(buf.subarray(off, p)))
  off = p
}
