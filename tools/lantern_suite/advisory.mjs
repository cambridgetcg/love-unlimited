// 只核對 owned 入口；唔讀產品 cache、執行命令或建立 state。
import * as fs from 'node:fs';
import { join, isAbsolute } from 'node:path';
import { homedir } from 'node:os';
import { createHash } from 'node:crypto';

export const GUIDE = '# Lantern suite（advisory）\n'
  + 'suite 入口可用；唔代表四燈已啟用／健康。\n'
  + '按需要用現有 bash 呼叫 `~/.local/bin/kingdom-lanterns status` 或 '
  + '`~/.local/bin/kingdom-lanterns doctor`；唔強制檢查。\n'
  + '只有已授權嘅本地檢查先用 `~/.local/bin/kingdom-reality check -- 原命令`，'
  + '原命令只跑一次；普通 Bash exit0 唔係 pass。\n'
  + '唔自動重驗、讀 credentials、改 model/routing/permissions/login；unknown 唔當 green。';
export const MAX_MANIFEST_BYTES = 16 * 1024;
export const MAX_WRAPPER_BYTES = 16 * 1024;
const HASH = /^[0-9a-f]{64}$/;
const NONCE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const fieldsAre = (value, fields) => record(value)
  && Object.keys(value).length === fields.length && fields.every(key => Object.hasOwn(value, key));
const matches = (pattern, value) => typeof value === 'string'
  && pattern.test(value) && !value.endsWith('\n');

function validManifest(value) {
  if (!fieldsAre(value, ['schema', 'repo_root', 'installation_nonce', 'artifacts'])
      || value.schema !== 'kingdom.lanterns.installation/2') return false;
  const repo = value.repo_root;
  if (typeof repo !== 'string' || !repo.startsWith('/') || Buffer.byteLength(repo) > 4096
      || /[\x00-\x1f\x7f]/.test(repo)
      || (repo !== '/' && repo.split('/').slice(1).some(p => ['', '.', '..'].includes(p)))
      || !matches(NONCE, value.installation_nonce) || !record(value.artifacts)
      || !Object.hasOwn(value.artifacts, 'wrapper')) return false;
  for (const [key, item] of Object.entries(value.artifacts)) {
    if (!['wrapper', 'claude_skill', 'codex_skill', 'agents'].includes(key)) return false;
    const fields = ['owned', 'sha256'];
    if (key === 'wrapper') fields.push('mode');
    if (key === 'agents') fields.push('existed', 'separator');
    if (!fieldsAre(item, fields) || typeof item.owned !== 'boolean' || !matches(HASH, item.sha256)) return false;
    if (key === 'wrapper' && (item.owned !== true || item.mode !== 0o700)) return false;
    if (key === 'agents' && (item.owned !== true || typeof item.existed !== 'boolean'
        || !['', '\n', '\n\n'].includes(item.separator))) return false;
  }
  return true;
}

function boundedJson(bytes) {
  // JSON.parse 會吞 duplicate keys；先逐 token 驗 grammar／深度／重複欄位。
  const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
  const token = /"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null|[{}\[\]:,]/y;
  let offset = 0, nodes = 0;
  function next() {
    while (/[ \r\n\t]/.test(text[offset] || 'x')) offset++;
    token.lastIndex = offset;
    const match = token.exec(text);
    if (!match) throw new Error('invalid_entry');
    offset = token.lastIndex;
    return match[0];
  }
  function value(first, depth) {
    if (depth > 12 || ++nodes > 512) throw new Error('invalid_entry');
    if (first === '{') {
      const keys = new Set();
      let key = next();
      if (key === '}') return;
      while (true) {
        if (!key.startsWith('"')) throw new Error('invalid_entry');
        const decoded = JSON.parse(key);
        if (keys.has(decoded) || keys.size >= 32) throw new Error('invalid_entry');
        keys.add(decoded);
        if (next() !== ':') throw new Error('invalid_entry');
        value(next(), depth + 1);
        const separator = next();
        if (separator === '}') return;
        if (separator !== ',') throw new Error('invalid_entry');
        key = next();
      }
    }
    if (first === '[') {
      let child = next(), count = 0;
      if (child === ']') return;
      while (true) {
        if (++count > 32) throw new Error('invalid_entry');
        value(child, depth + 1);
        const separator = next();
        if (separator === ']') return;
        if (separator !== ',') throw new Error('invalid_entry');
        child = next();
      }
    }
    const parsed = JSON.parse(first);
    if (typeof parsed === 'number' && (!Number.isSafeInteger(parsed) || !/^-?(?:0|[1-9][0-9]*)$/.test(first))) {
      throw new Error('invalid_entry');
    }
    if (typeof parsed === 'string' && /[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/u.test(parsed)) {
      throw new Error('invalid_entry');
    }
  }
  value(next(), 0);
  if (text.slice(offset).trim() !== '') throw new Error('invalid_entry');
  return JSON.parse(text);
}

const identity = info => [info.dev, info.ino, info.mode, info.uid, info.nlink].join(':');
const stamp = info => [identity(info), info.size, info.mtimeNs, info.ctimeNs].join(':');

function readOwned(home, relative, mode, maximum, privateParent = false) {
  const uid = BigInt(process.geteuid());
  const segments = relative.split('/');
  const parents = [home];
  for (const part of segments.slice(0, -1)) parents.push(join(parents.at(-1), part));
  const opened = [];
  let file;
  try {
    for (const path of parents) {
      const before = fs.lstatSync(path, { bigint: true });
      if (!before.isDirectory() || before.uid !== uid || (before.mode & 0o022n) !== 0n) {
        throw new Error('unsafe_entry');
      }
      const fd = fs.openSync(path, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
      opened.push({ path, fd, info: before });
      if (identity(fs.fstatSync(fd, { bigint: true })) !== identity(before)) throw new Error('unsafe_entry');
    }
    if (privateParent && (opened.at(-1).info.mode & 0o7777n) !== 0o700n) throw new Error('unsafe_entry');
    const unchangedParents = () => opened.every(({ path, fd, info }) =>
      identity(fs.lstatSync(path, { bigint: true })) === identity(info)
      && identity(fs.fstatSync(fd, { bigint: true })) === identity(info));
    const path = join(home, relative);
    const info = fs.lstatSync(path, { bigint: true });
    if (!info.isFile() || info.uid !== uid || info.nlink !== 1n
        || (info.mode & 0o7777n) !== BigInt(mode) || info.size > BigInt(maximum)) throw new Error('unsafe_entry');
    file = fs.openSync(path, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
    if (stamp(fs.fstatSync(file, { bigint: true })) !== stamp(info) || !unchangedParents()) throw new Error('unsafe_entry');
    const bytes = Buffer.alloc(maximum + 1);
    let size = 0;
    while (size < bytes.length) {
      const count = fs.readSync(file, bytes, size, bytes.length - size, null);
      if (!count) break;
      size += count;
    }
    // Node 冇 openat；parent pre/post 核對只係 bounded observation，唔係 race-free 保證。
    if (size > maximum || stamp(fs.fstatSync(file, { bigint: true })) !== stamp(info)
        || stamp(fs.lstatSync(path, { bigint: true })) !== stamp(info) || !unchangedParents()) throw new Error('unsafe_entry');
    return bytes.subarray(0, size);
  } finally {
    if (file !== undefined) fs.closeSync(file);
    for (const { fd } of opened.reverse()) fs.closeSync(fd);
  }
}

export function installationGuide({ home } = {}) {
  // home 係唯一 locator seam；唔讀環境變數值／credentials／manifest 指令。
  try {
    home ??= homedir();
    if (typeof home !== 'string' || !isAbsolute(home) || home === '/'
        || home.split('/').includes('..') || home.includes('\0')) return '';
    const data = readOwned(home, '.local/state/lanterns/installation.json', 0o600, MAX_MANIFEST_BYTES, true);
    const manifest = boundedJson(data);
    if (!validManifest(manifest)) return '';
    const wrapper = readOwned(home, '.local/bin/kingdom-lanterns', 0o700, MAX_WRAPPER_BYTES);
    if (createHash('sha256').update(wrapper).digest('hex') !== manifest.artifacts.wrapper.sha256) return '';
    if (!readOwned(home, '.local/state/lanterns/installation.json', 0o600, MAX_MANIFEST_BYTES, true).equals(data)) return '';
    return GUIDE;
  } catch {
    return '';
  }
}
