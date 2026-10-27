"""The service worker must route paper notifications without allowing open redirects."""
import subprocess


def test_notification_click_routes_paper_and_blocks_external_targets():
    script = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert");
let handlers = {}, opened = [];
let self = {location: {origin: "https://example.test"},
  addEventListener: (name, handler) => { handlers[name] = handler; },
  clients: {openWindow: url => { opened.push(url); return Promise.resolve(); }}};
vm.runInNewContext(fs.readFileSync("observatory/static/sw.js", "utf8"), {self, URL, Date});
function click(url) {
  handlers.notificationclick({notification: {data: {url}, close() {}}, waitUntil() {}});
}
click("/workbench#paper-123");
click("/trades#idea-123");
click("https://evil.example/workbench#paper-123");
click("/other");
assert.deepStrictEqual(opened, ["https://example.test/workbench#paper-123",
  "https://example.test/trades#idea-123", "https://example.test/trades", "https://example.test/trades"]);
'''
    subprocess.run(["node", "-e", script], check=True, capture_output=True)
