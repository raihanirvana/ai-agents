// Trusted static preview of immutable build output. No target code on the host.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = '/site';
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };
http.createServer((req, res) => {
  let filename;
  try {
    const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    filename = path.resolve(root, '.' + pathname);
    if (filename !== root && !filename.startsWith(root + '/')) throw new Error('outside root');
    if (filename === root) filename += '/index.html';
    const stat = fs.lstatSync(filename);
    if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('not a regular file');
    res.writeHead(200, { 'Content-Type': mime[path.extname(filename)] || 'application/octet-stream',
      'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
    fs.createReadStream(filename).pipe(res);
  } catch {
    res.writeHead(404); res.end('Not found');
  }
}).listen(4173, '0.0.0.0');
