import QtQuick
import QtQuick.Controls

// A run's work graph, left to right: scout, architect, the plan gate, a
// lane per slice (builder, then reviewer; a loop-back arrow "try 2/2" when a
// REJECT sent it back), then for a diamond the merge gate and integrator.
// The layout comes from Python (present_fleet.graph); boxes are a keyed
// model, so they update in place; edges are drawn on a Canvas. `fleets` and
// `theme` come from Python.
Flickable {
  id: graphView
  property string runId: ""
  property string chosen: ""  // a node key
  signal pick(string key)

  readonly property int colWidth: 150
  readonly property int laneHeight: 72
  readonly property int boxWidth: 128
  readonly property int boxHeight: 48
  property var layout: ({ nodes: [], edges: [], columns: 0, lanes: 1 })

  function relayout() { layout = runId ? fleets.graph(runId) : ({ nodes: [], edges: [], columns: 0, lanes: 1 }); edges.requestPaint() }
  onRunIdChanged: relayout()
  Connections { target: fleets; function onRunChanged(id) { if (id === graphView.runId) graphView.relayout() } }

  function position(key) {
    const n = layout.nodes.find(n => n.key === key)
    return n ? { x: 16 + n.col * colWidth, y: 16 + n.lane * laneHeight } : null
  }

  // Shrunk to fit the width, down to 70%; wider than that, it scrolls.
  readonly property real fullWidth: 32 + Math.max(1, layout.columns) * colWidth
  readonly property real fullHeight: 40 + Math.max(1, layout.lanes) * laneHeight
  readonly property real zoom: Math.max(0.7, Math.min(1, width / fullWidth))
  clip: true
  contentWidth: fullWidth * zoom
  contentHeight: fullHeight * zoom
  implicitHeight: Math.min(contentHeight, 420)
  boundsBehavior: Flickable.StopAtBounds
  ScrollBar.horizontal: ScrollBar {}
  ScrollBar.vertical: ScrollBar {}

  Item {
  id: stage
  width: graphView.fullWidth
  height: graphView.fullHeight
  scale: graphView.zoom
  transformOrigin: Item.TopLeft

  Canvas {
    id: edges
    width: graphView.fullWidth
    height: graphView.fullHeight
    onPaint: {
      const ctx = getContext("2d")
      ctx.reset()
      ctx.lineWidth = 1.5
      ctx.font = "11px '" + fontFamily + "'"
      for (const e of graphView.layout.edges) {
        const a = graphView.position(e.from), b = graphView.position(e.to)
        if (!a || !b) continue
        if (e.kind === "back") {
          // Under the lane, from the reviewer back to its builder.
          const x1 = a.x + graphView.boxWidth / 2, x2 = b.x + graphView.boxWidth / 2
          const y = a.y + graphView.boxHeight
          ctx.strokeStyle = theme.urgent
          ctx.fillStyle = theme.urgent
          ctx.beginPath()
          ctx.moveTo(x1, y)
          ctx.bezierCurveTo(x1, y + 22, x2, y + 22, x2, y + 4)
          ctx.stroke()
          ctx.beginPath()
          ctx.moveTo(x2, y); ctx.lineTo(x2 - 4, y + 7); ctx.lineTo(x2 + 4, y + 7); ctx.closePath(); ctx.fill()
          ctx.fillText(e.label, (x1 + x2) / 2 - 18, y + 22)
          continue
        }
        if (e.kind === "next") {
          // Down from the reviewer, back left to the next slice's builder.
          const sx = a.x + graphView.boxWidth / 2, sy = a.y + graphView.boxHeight
          const tx = b.x + graphView.boxWidth / 2, ty = b.y
          ctx.strokeStyle = theme.muted
          ctx.fillStyle = theme.muted
          ctx.setLineDash([4, 3])
          ctx.beginPath()
          ctx.moveTo(sx, sy)
          ctx.bezierCurveTo(sx, sy + 14, tx, ty - 14, tx, ty - 4)
          ctx.stroke()
          ctx.setLineDash([])
          ctx.beginPath()
          ctx.moveTo(tx, ty); ctx.lineTo(tx - 4, ty - 7); ctx.lineTo(tx + 4, ty - 7); ctx.closePath(); ctx.fill()
          continue
        }
        const sx = a.x + graphView.boxWidth, sy = a.y + graphView.boxHeight / 2
        const tx = b.x, ty = b.y + graphView.boxHeight / 2
        ctx.strokeStyle = theme.muted
        ctx.fillStyle = theme.muted
        ctx.beginPath()
        ctx.moveTo(sx, sy)
        const mid = (sx + tx) / 2
        ctx.bezierCurveTo(mid, sy, mid, ty, tx - 4, ty)
        ctx.stroke()
        ctx.beginPath()
        ctx.moveTo(tx, ty); ctx.lineTo(tx - 7, ty - 4); ctx.lineTo(tx - 7, ty + 4); ctx.closePath(); ctx.fill()
      }
    }
  }

  Repeater {
    model: graphView.runId ? fleets.graphNodes(graphView.runId) : null
    delegate: Rectangle {
      id: box
      required property var item
      required property string key
      objectName: "graph-" + key
      x: 16 + item.col * graphView.colWidth
      y: 16 + item.lane * graphView.laneHeight
      width: graphView.boxWidth
      height: graphView.boxHeight
      radius: item.key.endsWith("gate") ? height / 2 : 6
      readonly property bool bad: ["rejected", "failed", "held", "you", "stalled"].indexOf(item.state) >= 0
      color: graphView.chosen === key ? theme.selection : theme.surface
      border.width: item.state === "queued" || item.state === "skipped" ? 1 : 2
      border.color: bad ? theme.urgent : item.state === "running" ? theme.accent
                    : item.state === "passed" ? theme.foreground : theme.selection
      opacity: item.state === "skipped" ? 0.45 : 1
      Behavior on x { NumberAnimation { duration: 200 } }
      Behavior on y { NumberAnimation { duration: 200 } }

      SequentialAnimation on border.width {
        running: box.item.state === "running" || box.item.state === "you"
        loops: Animation.Infinite
        NumberAnimation { to: 3; duration: 600 }
        NumberAnimation { to: 1.5; duration: 600 }
      }

      Column {
        anchors.centerIn: parent
        width: parent.width - 12
        Label {
          width: parent.width
          text: ({ passed: "✓ ", rejected: "✗ ", failed: "✗ ", you: "● ", held: "‖ " }[box.item.state] || "") + box.item.label
          color: box.bad ? theme.urgent : theme.foreground
          horizontalAlignment: Text.AlignHCenter
          elide: Text.ElideRight
          font.pixelSize: 13
        }
        Label {
          width: parent.width
          text: box.item.state === "you" ? "waits for you" : box.item.state === "stalled" ? "no sign of life" : box.item.sub
          color: box.bad ? theme.urgent : theme.muted
          horizontalAlignment: Text.AlignHCenter
          elide: Text.ElideRight
          font.pixelSize: 11
        }
      }

      MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        enabled: !box.key.endsWith("gate")
        onClicked: graphView.pick(box.key)
      }
    }
  }
  }
}
