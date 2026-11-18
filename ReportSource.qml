import QtQuick
import Quickshell.Io

Item {
  id: root
  property bool demo: false
  property var report: ({drives: [], errors: [], stale: true})
  property bool refreshPending: false
  function refresh() {
    if (reader.running) refreshPending = true
    else reader.running = true
  }
  onDemoChanged: refresh()
  Component.onCompleted: refresh()
  Process {
    id: reader
    command: ["python3", "-I", decodeURIComponent(Qt.resolvedUrl("backend/drive_health.py").toString().replace(/^file:\/\//, ""))].concat(root.demo ? ["--demo"] : [])
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          if (text.length > 2097152) throw new Error("Report exceeds size limit")
          var parsed = JSON.parse(text)
          if (parsed.schema !== 1 || !Array.isArray(parsed.drives) || parsed.drives.length > 64 || !Array.isArray(parsed.errors)) throw new Error("Invalid report")
          root.report = parsed
        } catch (error) {
          root.report = {drives: [], errors: [String(error).slice(0, 160)], stale: true}
        }
      }
    }
    onExited: function(code) {
      if (root.refreshPending) Qt.callLater(function() { root.refreshPending = false; root.refresh() })
      if (code !== 0) root.report = {drives: [], errors: ["Report reader exited: " + code], stale: true}
    }
  }
  Timer { interval: 60000; running: true; repeat: true; onTriggered: root.refresh() }
}
