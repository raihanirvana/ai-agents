// Preview server for the verified build. Same serving rules as static-server.cjs (the acceptance harness), but it
// listens on a unix socket: the container has no network at all, and only the supervisor's loopback proxy can reach it.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = '/site';
const socket = process.env.PREVIEW_SOCKET;
if (!socket) throw Error('PREVIEW_SOCKET is required');
const types = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml'};
const server = http.createServer((req, res) => {
  try {
    if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405, {Allow: 'GET, HEAD'}); return res.end(); }
    const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    let file = path.resolve(root, '.' + pathname);
    if (file !== root && !file.startsWith(root + '/')) throw Error('outside site');
    const checked = (p) => {
      let current = root;
      for (const part of path.relative(root, p).split('/').filter(Boolean)) {
        current = path.join(current, part);
        if (fs.lstatSync(current).isSymbolicLink()) throw Error('symlink');
      }
      return p;
    };
    checked(file);
    if (fs.statSync(file).isDirectory()) file = checked(path.join(file, 'index.html'));
    const stat = fs.statSync(file);
    if (!stat.isFile() || stat.size > 8 * 1024 * 1024) throw Error('not a bounded file');
    res.writeHead(200, {'Content-Type': types[path.extname(file)] || 'application/octet-stream',
      'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'});
    if (req.method === 'HEAD') res.end(); else fs.createReadStream(file).pipe(res);
  } catch { res.writeHead(404); res.end(); }
});
server.listen(socket, () => fs.chmodSync(socket, 0o666));
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => server.close(() => process.exit(0)));
