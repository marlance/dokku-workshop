// Minimal workshop app. Buildpacks detect Node from package.json — no Dockerfile.
// Reads two env vars so attendees can see config changes without a rebuild:
//   BG_COLOR  (default "white")   -> page background
//   ATTENDEE  (default "world")   -> name in the greeting
const http = require("http");

const port = process.env.PORT || 5000;
const bg = process.env.BG_COLOR || "white";
const who = process.env.ATTENDEE || "world";

const server = http.createServer((req, res) => {
  if (req.url === "/healthz") {
    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end("ok");
    return;
  }
  const html = `<!doctype html>
<html><head><meta charset="utf-8"><title>Dokku workshop</title>
<style>
  html,body{height:100%;margin:0}
  body{background:${bg};display:flex;align-items:center;justify-content:center;
       font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
  .card{background:rgba(255,255,255,.85);padding:40px 48px;border-radius:16px;
        box-shadow:0 10px 40px rgba(0,0,0,.15);text-align:center}
  h1{margin:0 0 8px;color:#13293D}
  p{margin:0;color:#5b6670}
</style></head>
<body><div class="card">
  <h1>Hello ${escapeHtml(who)}, deployed with Dokku!</h1>
  <p>Background is <code>${escapeHtml(bg)}</code>. Change it with <code>config:set BG_COLOR=...</code></p>
</div></body></html>`;
  res.writeHead(200, { "Content-Type": "text/html" });
  res.end(html);
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

server.listen(port, () => console.log(`listening on ${port}, bg=${bg}, attendee=${who}`));
