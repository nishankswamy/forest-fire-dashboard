const pptxgen = require('pptxgenjs');

// ---- palette: forest canopy + ember -------------------------------------
// Deep forest dominates; moss supports; ember is the single sharp accent,
// used only for fire, alerts, and the one number that matters on a slide.
const C = {
  forest:  '14301F',   // dominant dark
  forest2: '1E4630',   // dark panel
  moss:    '6FA07A',
  moss2:   '9CC0A4',
  ember:   'E8622C',   // accent
  ash:     'F3F6F2',   // light bg
  white:   'FFFFFF',
  ink:     '13291C',
  muted:   '6E7D71',
  alert:   'C1352B',
  sky:     '3E7CB1'    // routing / data hops
};

const HEAD = 'Cambria';
const BODY = 'Calibri';
const W = 13.3, H = 7.5;

const pres = new pptxgen();
pres.layout = 'LAYOUT_WIDE';
pres.author = 'Nishank Swamy';
pres.title = 'Forest Fire Detection Using a LoRa Wireless Sensor Network';

// ---- helpers -------------------------------------------------------------

function darkSlide() {
  const s = pres.addSlide();
  s.background = { color: C.forest };
  return s;
}
function lightSlide() {
  const s = pres.addSlide();
  s.background = { color: C.ash };
  return s;
}

// Motif: a small filled circle, echoing the node markers on the dashboard map.
function nodeDot(s, x, y, d, color, label, labelColor) {
  s.addShape(pres.ShapeType.ellipse, {
    x, y, w: d, h: d, fill: { color }
  });
  if (label) {
    s.addText(label, {
      x, y, w: d, h: d, align: 'center', valign: 'middle', margin: 0,
      fontFace: BODY, fontSize: d > 0.5 ? 12 : 9, bold: true,
      color: labelColor || C.forest
    });
  }
}

function title(s, text, opts) {
  const o = Object.assign({
    x: 0.6, y: 0.42, w: W - 1.2, h: 0.85, margin: 0,
    fontFace: HEAD, fontSize: 34, bold: true, color: C.ink
  }, opts || {});
  s.addText(text, o);
}

function kicker(s, text, color) {
  s.addText(text.toUpperCase(), {
    x: 0.6, y: 0.14, w: W - 1.2, h: 0.3, margin: 0,
    fontFace: BODY, fontSize: 11.5, bold: true, charSpacing: 2.2,
    color: color || C.moss
  });
}

function card(s, x, y, w, h, fill) {
  s.addShape(pres.ShapeType.roundRect, {
    x, y, w, h, rectRadius: 0.08,
    fill: { color: fill || C.white },
    line: { color: 'E2E8E3', width: 1 },
    shadow: { type: 'outer', angle: 90, blur: 8, offset: 1, opacity: 0.07, color: '000000' }
  });
}

function stat(s, x, y, w, value, label, color, size) {
  s.addText(value, {
    x, y, w, h: 0.85, margin: 0, align: 'left',
    fontFace: HEAD, fontSize: size || 46, bold: true, color: color || C.ember
  });
  s.addText(label, {
    x, y: y + 0.82, w, h: 0.5, margin: 0, align: 'left',
    fontFace: BODY, fontSize: 12, color: C.muted
  });
}

function bullets(s, items, x, y, w, h, size, color) {
  s.addText(items.map((t, i) => ({
    text: t, options: { bullet: true, breakLine: i < items.length - 1 }
  })), {
    x, y, w, h, margin: 0, fontFace: BODY, fontSize: size || 14,
    color: color || C.ink, paraSpaceAfter: 9, lineSpacing: 20
  });
}

// =========================================================================
// 1 — TITLE
// =========================================================================
{
  const s = darkSlide();

  // node-field motif, faint, upper right
  const pts = [[10.2,1.0],[11.4,1.5],[12.4,1.1],[10.8,2.3],[12.0,2.6],[11.2,3.4],[12.6,3.9]];
  pts.forEach((p, i) => nodeDot(s, p[0], p[1], i % 3 === 0 ? 0.30 : 0.20,
                               i === 3 ? C.ember : C.moss));
  [[10.2,1.0,11.4,1.5],[11.4,1.5,12.4,1.1],[10.8,2.3,11.4,1.5],
   [10.8,2.3,12.0,2.6],[12.0,2.6,11.2,3.4],[11.2,3.4,12.6,3.9]].forEach(l => {
    s.addShape(pres.ShapeType.line, {
      x: l[0] + 0.12, y: l[1] + 0.12, w: l[2] - l[0], h: l[3] - l[1],
      line: { color: C.moss, width: 1, transparency: 55 }
    });
  });

  s.addText('Forest Fire Detection', {
    x: 0.85, y: 2.05, w: 8.6, h: 0.95, margin: 0,
    fontFace: HEAD, fontSize: 44, bold: true, color: C.white
  });
  s.addText('Using a LoRa Wireless Sensor Network', {
    x: 0.85, y: 2.95, w: 8.6, h: 0.7, margin: 0,
    fontFace: HEAD, fontSize: 27, color: C.moss2
  });
  s.addText('Six Raspberry Pi nodes · two-cluster multi-hop topology · TDMA scheduling',
    { x: 0.85, y: 3.85, w: 8.8, h: 0.4, margin: 0,
      fontFace: BODY, fontSize: 14, color: C.moss });

  s.addText('Nishank Swamy', {
    x: 0.85, y: 5.55, w: 6, h: 0.35, margin: 0,
    fontFace: BODY, fontSize: 15, bold: true, color: C.white });
  s.addText('Final Year Project  ·  September 2026', {
    x: 0.85, y: 5.92, w: 6, h: 0.35, margin: 0,
    fontFace: BODY, fontSize: 12, color: C.moss });

  s.addNotes('Ground-based fire detection. Six Pis, LoRa radio, multi-hop. ' +
    'The two things I want to land today: collisions and routing dead ends are ' +
    'eliminated by construction, not by retrying.');
}

// =========================================================================
// 2 — THE PROBLEM
// =========================================================================
{
  const s = lightSlide();
  kicker(s, 'The problem', C.ember);
  title(s, 'Detection delay decides how much burns');

  s.addText('Fire spreads faster than it is found. Area affected grows roughly with ' +
    'the square of elapsed time in the early phase, so cutting detection from hours ' +
    'to minutes has a disproportionate effect on final size.', {
    x: 0.6, y: 1.45, w: 6.4, h: 1.2, margin: 0,
    fontFace: BODY, fontSize: 14.5, color: C.ink, lineSpacing: 22 });

  const rows = [
    ['Satellite thermal', 'Wide coverage, but revisits a few times a day and cloud blocks it entirely'],
    ['Watchtower / camera', 'Continuous, but needs line of sight and only sees a plume once it clears the canopy'],
    ['Ground sensors', 'Detect temperature rise, humidity collapse and combustion gas AT the origin — before a plume forms']
  ];
  rows.forEach((r, i) => {
    const y = 3.05 + i * 1.32;
    card(s, 0.6, y, 6.4, 1.12, i === 2 ? 'E9F1EA' : C.white);
    nodeDot(s, 0.85, y + 0.36, 0.4, i === 2 ? C.moss : 'D6DED8',
            String(i + 1), i === 2 ? C.white : C.muted);
    s.addText(r[0], { x: 1.42, y: y + 0.18, w: 4.9, h: 0.3, margin: 0,
      fontFace: BODY, fontSize: 13.5, bold: true, color: i === 2 ? C.forest : C.ink });
    s.addText(r[1], { x: 1.42, y: y + 0.5, w: 5.3, h: 0.55, margin: 0,
      fontFace: BODY, fontSize: 11.5, color: C.muted, lineSpacing: 15 });
  });

  card(s, 7.4, 1.45, 5.3, 5.5, C.forest2);
  s.addText('The catch', { x: 7.8, y: 1.8, w: 4.5, h: 0.35, margin: 0,
    fontFace: BODY, fontSize: 11.5, bold: true, charSpacing: 1.8, color: C.moss2 });
  s.addText('Forest interiors have no cellular coverage and no mains power.', {
    x: 7.8, y: 2.2, w: 4.5, h: 0.9, margin: 0,
    fontFace: HEAD, fontSize: 21, bold: true, color: C.white, lineSpacing: 27 });
  s.addText('Ground sensing only works if the communication problem is solved first. ' +
    'That is what this project is actually about.', {
    x: 7.8, y: 3.25, w: 4.5, h: 0.9, margin: 0,
    fontFace: BODY, fontSize: 13, color: C.moss2, lineSpacing: 20 });

  stat(s, 7.8, 4.55, 2.0, '~5 km', 'LoRa range,\nopen terrain', C.ember, 32);
  stat(s, 10.1, 4.55, 2.3, '2.4 kbps', 'air rate at\nthat range', C.moss2, 32);
  s.addText('Very low data rate for very long range — which suits telemetry, ' +
    'where a reading is a handful of bytes.', {
    x: 7.8, y: 5.95, w: 4.5, h: 0.7, margin: 0,
    fontFace: BODY, fontSize: 11.5, italic: true, color: C.moss, lineSpacing: 16 });

  s.addNotes('Three approaches. Ground sensing wins on latency but loses on ' +
    'communication — no cell, no mains. LoRa solves it: kilometres of range at ' +
    'battery power, in exchange for a data rate that would be useless for anything ' +
    'except sensor readings.');
}

// =========================================================================
// 3 — OBJECTIVES
// =========================================================================
{
  const s = lightSlide();
  kicker(s, 'Objectives');
  title(s, 'What the system has to do');

  const objs = [
    ['Sense', 'Temperature, humidity and smoke at distributed points, with the fire decision made on the node itself'],
    ['Reach', 'Deliver to a gateway with no cellular or mains infrastructure — including from nodes out of gateway range'],
    ['Never collide', 'Multiple nodes share one radio channel. Packet collisions must be impossible, not merely unlikely'],
    ['Never dead-end', 'A reading with a valid path to the gateway must not be lost to a routing dead end'],
    ['Survive failure', 'A cluster head going down must not take its whole cluster off the map'],
    ['Last on battery', 'Energy consumption low enough that battery operation is viable']
  ];

  objs.forEach((o, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = 0.6 + col * 6.25, y = 1.5 + row * 1.72;
    card(s, x, y, 6.0, 1.5);
    nodeDot(s, x + 0.3, y + 0.5, 0.5, i < 2 ? C.moss : (i < 4 ? C.ember : C.sky),
            String(i + 1), C.white);
    s.addText(o[0], { x: x + 1.0, y: y + 0.24, w: 4.7, h: 0.34, margin: 0,
      fontFace: BODY, fontSize: 15, bold: true, color: C.ink });
    s.addText(o[1], { x: x + 1.0, y: y + 0.6, w: 4.75, h: 0.75, margin: 0,
      fontFace: BODY, fontSize: 11.5, color: C.muted, lineSpacing: 15 });
  });

  s.addNotes('Six objectives. Three and four are the interesting ones — note the ' +
    'wording: impossible, not unlikely. That distinction drives every design choice ' +
    'that follows.');
}

// =========================================================================
// 4 — ARCHITECTURE
// =========================================================================
{
  const s = lightSlide();
  kicker(s, 'System architecture');
  title(s, 'Six Pis, two clusters, three hops');

  const NY = 2.15;   // node row
  const HY = 3.75;   // head row
  const GY = 5.5;    // gateway

  // cluster A members
  nodeDot(s, 1.5, NY, 0.62, C.moss, '2', C.white);
  nodeDot(s, 3.1, NY, 0.62, C.moss, '3', C.white);
  // heads
  nodeDot(s, 2.25, HY, 0.82, C.forest2, 'CH-A', C.white);
  nodeDot(s, 7.6, HY, 0.82, C.forest2, 'CH-B', C.white);
  // cluster B member
  nodeDot(s, 9.5, NY, 0.62, C.moss, '5', C.white);
  // gateway
  s.addShape(pres.ShapeType.roundRect, {
    x: 1.85, y: GY, w: 1.6, h: 0.72, rectRadius: 0.1,
    fill: { color: C.ember } });
  s.addText('GATEWAY', { x: 1.85, y: GY, w: 1.6, h: 0.72, margin: 0,
    align: 'center', valign: 'middle', fontFace: BODY, fontSize: 12,
    bold: true, color: C.white });

  const link = (x1, y1, x2, y2, color, width, dash) => {
    s.addShape(pres.ShapeType.line, {
      x: Math.min(x1, x2), y: Math.min(y1, y2),
      w: Math.abs(x2 - x1), h: Math.abs(y2 - y1),
      line: Object.assign({ color, width: width || 2 },
                          dash ? { dashType: dash } : {}),
      flipH: x2 < x1, flipV: y2 < y1
    });
  };
  link(1.81, NY + 0.62, 2.45, HY, C.muted, 1.5, 'dash');
  link(3.41, NY + 0.62, 2.87, HY, C.muted, 1.5, 'dash');
  link(9.81, NY + 0.62, 8.22, HY, C.muted, 1.5, 'dash');
  link(7.6, HY + 0.41, 3.07, HY + 0.41, C.sky, 2.5);
  link(2.66, HY + 0.82, 2.66, GY, C.sky, 2.5);

  s.addText('relay  CH-B → CH-A', { x: 4.2, y: HY - 0.05, w: 2.6, h: 0.3, margin: 0,
    align: 'center', fontFace: BODY, fontSize: 10.5, bold: true, color: C.sky });

  s.addText('CLUSTER A', { x: 1.3, y: 1.62, w: 2.4, h: 0.28, margin: 0,
    fontFace: BODY, fontSize: 10.5, bold: true, charSpacing: 1.6, color: C.muted });
  s.addText('CLUSTER B', { x: 8.6, y: 1.62, w: 2.4, h: 0.28, margin: 0,
    fontFace: BODY, fontSize: 10.5, bold: true, charSpacing: 1.6, color: C.muted });

  card(s, 9.05, 3.3, 3.65, 3.1, C.forest2);
  s.addText('CH-B sits outside gateway range on purpose', {
    x: 9.35, y: 3.55, w: 3.05, h: 0.85, margin: 0,
    fontFace: HEAD, fontSize: 16, bold: true, color: C.white, lineSpacing: 21 });
  s.addText('Without it, head-to-head forwarding would never be exercised and the ' +
    'multi-hop capability would go untested. Node 5 reaches the gateway in three hops:',
    { x: 9.35, y: 4.5, w: 3.05, h: 1.1, margin: 0,
      fontFace: BODY, fontSize: 11.5, color: C.moss2, lineSpacing: 16 });
  s.addText('5  →  CH-B  →  CH-A  →  GW', {
    x: 9.35, y: 5.72, w: 3.05, h: 0.4, margin: 0,
    fontFace: BODY, fontSize: 13, bold: true, color: C.ember });

  s.addNotes('Two clusters. Members report to their head, heads forward to the ' +
    'gateway. CH-B is deliberately out of gateway range — that is what makes the ' +
    'relay real rather than decorative. Node 5 takes three hops.');
}

// =========================================================================
// 5 — HARDWARE
// =========================================================================
{
  const s = lightSlide();
  kicker(s, 'Hardware');
  title(s, 'Per node, and the traps in it');

  const parts = [
    ['Raspberry Pi 4', '× 6', 'gateway, 2 heads, 3 nodes'],
    ['SX1262 868M LoRa HAT', '× 6', 'UART, private point-to-point'],
    ['DHT22', '× 5', 'temperature + humidity'],
    ['MQ-2', '× 5', 'smoke / combustible gas'],
    ['MCP3008', '× 5', 'ADC over SPI']
  ];
  parts.forEach((p, i) => {
    const y = 1.5 + i * 0.92;
    card(s, 0.6, y, 6.1, 0.76);
    s.addText(p[0], { x: 0.9, y: y + 0.09, w: 3.5, h: 0.3, margin: 0,
      fontFace: BODY, fontSize: 13, bold: true, color: C.ink });
    s.addText(p[2], { x: 0.9, y: y + 0.4, w: 4.3, h: 0.28, margin: 0,
      fontFace: BODY, fontSize: 10.5, color: C.muted });
    s.addText(p[1], { x: 5.5, y: y + 0.2, w: 0.9, h: 0.35, margin: 0,
      align: 'right', fontFace: HEAD, fontSize: 16, bold: true, color: C.moss });
  });

  const traps = [
    ['The Pi has no analogue input', 'The MQ-2’s analogue output cannot be read directly — hence the MCP3008 over SPI.'],
    ['5 V sensor into a 3.3 V ADC', 'The MQ-2 runs on 5 V and its output can exceed 3.3 V, which would destroy the MCP3008. A divider is mandatory.'],
    ['Jumper caps must come off', 'The HAT’s mode pins read HIGH when uncapped. With caps fitted the Pi cannot control radio power at all.'],
    ['866 MHz, not 868', 'India licenses 865–867 MHz. 868 is the European band — the module is tunable, the marketing name is not the channel.']
  ];
  traps.forEach((t, i) => {
    const y = 1.5 + i * 1.28;
    card(s, 7.1, y, 5.6, 1.12, C.white);
    nodeDot(s, 7.35, y + 0.36, 0.38, C.ember, '!', C.white);
    s.addText(t[0], { x: 7.88, y: y + 0.14, w: 4.6, h: 0.3, margin: 0,
      fontFace: BODY, fontSize: 12.5, bold: true, color: C.ink });
    s.addText(t[1], { x: 7.88, y: y + 0.46, w: 4.6, h: 0.6, margin: 0,
      fontFace: BODY, fontSize: 10.5, color: C.muted, lineSpacing: 14 });
  });

  s.addNotes('Bill of materials on the left. On the right, four constraints that ' +
    'each cost time to discover. The 5 V into 3.3 V one destroys hardware if missed.');
}

// =========================================================================
// 6 — TWO PROBLEMS
// =========================================================================
{
  const s = darkSlide();
  kicker(s, 'The two hard problems', C.moss2);
  title(s, 'Both solved by construction, not by retry', { color: C.white });

  const cols = [
    ['Collisions', 'Multiple nodes, one channel. Two transmissions overlapping in time corrupt each other.',
     'TDMA superframe', 'Every transmission occupies a slot owned by exactly one radio. Overlap is structurally impossible.', C.ember],
    ['Local minima', 'Greedy geographic routing stalls where no neighbour is closer to the sink — even when a path exists.',
     'Explicit routing tables', 'No geometric decision is made, so there is no minimum to reach. Ordered fallbacks repair broken links.', C.sky]
  ];

  cols.forEach((c, i) => {
    const x = 0.7 + i * 6.15;
    card(s, x, 1.55, 5.85, 5.15, C.forest2);
    s.addText(c[0], { x: x + 0.42, y: 1.85, w: 5.0, h: 0.5, margin: 0,
      fontFace: HEAD, fontSize: 24, bold: true, color: c[4] });
    s.addText('THE PROBLEM', { x: x + 0.42, y: 2.45, w: 5.0, h: 0.25, margin: 0,
      fontFace: BODY, fontSize: 9.5, bold: true, charSpacing: 1.6, color: C.moss });
    s.addText(c[1], { x: x + 0.42, y: 2.72, w: 5.0, h: 0.95, margin: 0,
      fontFace: BODY, fontSize: 13, color: C.white, lineSpacing: 19 });

    s.addText('OUR ANSWER', { x: x + 0.42, y: 3.95, w: 5.0, h: 0.25, margin: 0,
      fontFace: BODY, fontSize: 9.5, bold: true, charSpacing: 1.6, color: C.moss });
    s.addText(c[2], { x: x + 0.42, y: 4.22, w: 5.0, h: 0.42, margin: 0,
      fontFace: HEAD, fontSize: 19, bold: true, color: C.white });
    s.addText(c[3], { x: x + 0.42, y: 4.75, w: 5.0, h: 1.0, margin: 0,
      fontFace: BODY, fontSize: 12.5, color: C.moss2, lineSpacing: 18 });

    nodeDot(s, x + 0.42, 5.95, 0.42, c[4], '✓', C.white);
    s.addText(i === 0 ? '0 collisions in 184 transmissions'
                      : 'Cannot arise — proven by design',
      { x: x + 1.0, y: 6.02, w: 4.4, h: 0.32, margin: 0,
        fontFace: BODY, fontSize: 12, bold: true, color: C.white });
  });

  s.addNotes('This is the core of the project. Note the framing on both: not ' +
    '"we retry until it works" but "the failure cannot occur". That is a stronger ' +
    'claim and it is the one I can defend.');
}

// =========================================================================
// 7 — WHY TDMA
// =========================================================================
{
  const s = lightSlide();
  kicker(s, 'Design decision 1', C.ember);
  title(s, 'Why TDMA and not carrier sense');

  s.addText('CSMA would not have worked here — and the reason is specific, not general.', {
    x: 0.6, y: 1.4, w: 8.2, h: 0.4, margin: 0,
    fontFace: BODY, fontSize: 14.5, italic: true, color: C.muted });

  // hidden terminal picture
  card(s, 0.6, 2.0, 7.4, 3.0, C.white);
  nodeDot(s, 1.15, 3.2, 0.6, C.moss, '2', C.white);
  nodeDot(s, 3.85, 3.2, 0.75, C.forest2, 'CH-A', C.white);
  nodeDot(s, 6.75, 3.2, 0.6, C.moss, 'CH-B', C.white);

  const arrow = (x, w, color) => s.addShape(pres.ShapeType.line, {
    x, y: 3.5, w, h: 0,
    line: { color, width: 2.5, endArrowType: 'triangle' } });
  arrow(1.85, 1.9, C.sky);
  s.addShape(pres.ShapeType.line, {
    x: 4.72, y: 3.5, w: 1.95, h: 0,
    line: { color: C.sky, width: 2.5, beginArrowType: 'triangle' } });

  s.addShape(pres.ShapeType.line, {
    x: 1.5, y: 4.35, w: 5.4, h: 0,
    line: { color: C.alert, width: 2, dashType: 'dash' } });
  s.addText('node 2 and CH-B cannot hear each other', {
    x: 1.5, y: 4.42, w: 5.4, h: 0.3, margin: 0, align: 'center',
    fontFace: BODY, fontSize: 11, bold: true, color: C.alert });

  s.addText('Both are heard by CH-A. Under CSMA, node 2 senses a clear channel — ' +
    'because it genuinely cannot hear CH-B transmitting — and keys up. Both collide ' +
    'at CH-A. Carrier sense cannot prevent this: the information needed is not ' +
    'available at the sensing node.', {
    x: 0.95, y: 2.28, w: 6.7, h: 0.85, margin: 0,
    fontFace: BODY, fontSize: 11.5, color: C.muted, lineSpacing: 16 });

  card(s, 8.4, 2.0, 4.3, 4.7, C.forest2);
  s.addText('What slots buy', { x: 8.75, y: 2.28, w: 3.6, h: 0.4, margin: 0,
    fontFace: HEAD, fontSize: 18, bold: true, color: C.white });
  bullets(s, [
    'Collisions impossible by construction',
    'No contention window, no backoff, no wasted carrier sensing',
    'Radios can sleep on a schedule — the entire energy story',
    'A slot map is a static object that can be verified before deployment'
  ], 8.75, 2.85, 3.6, 2.6, 12, C.moss2);

  s.addText('Cost: time synchronisation, and a fixed known node set. ' +
    'Both acceptable at six nodes.', {
    x: 8.75, y: 5.75, w: 3.6, h: 0.7, margin: 0,
    fontFace: BODY, fontSize: 11, italic: true, color: C.moss, lineSpacing: 15 });

  s.addNotes('The hidden terminal pair is the whole argument. If asked why not ' +
    'CSMA — node 2 and CH-B are mutually deaf but both heard by CH-A. Carrier ' +
    'sense is blind to exactly the case that matters.');
}

// =========================================================================
// 8 — SUPERFRAME
// =========================================================================
{
  const s = lightSlide();
  kicker(s, 'The schedule');
  title(s, 'One superframe: 60 seconds, ten slots');

  const slots = [
    ['0', 'GW', 'beacon broadcast', C.ember],
    ['1', 'CH-A', 'rebroadcast beacon', C.ember],
    ['2', 'CH-B', 'rebroadcast beacon', C.ember],
    ['3', 'CH-A', 'own reading → GW', C.sky],
    ['4', 'node 2', 'reading → CH-A', C.moss],
    ['5', 'node 3', 'reading → CH-A', C.moss],
    ['6', 'CH-B', 'own reading → CH-A', C.sky],
    ['7', 'node 5', 'reading → CH-B', C.moss],
    ['8', 'CH-B', 'forward cluster B', C.sky],
    ['9', 'CH-A', 'forward everything', C.sky]
  ];
  slots.forEach((sl, i) => {
    const x = 0.6 + i * 1.05;
    s.addShape(pres.ShapeType.roundRect, {
      x, y: 1.55, w: 0.95, h: 1.5, rectRadius: 0.06,
      fill: { color: sl[3] } });
    s.addText(sl[0], { x, y: 1.65, w: 0.95, h: 0.35, margin: 0, align: 'center',
      fontFace: HEAD, fontSize: 17, bold: true, color: C.white });
    s.addText(sl[1], { x, y: 2.05, w: 0.95, h: 0.3, margin: 0, align: 'center',
      fontFace: BODY, fontSize: 10.5, bold: true, color: C.white });
    s.addText(sl[2], { x: x + 0.05, y: 2.35, w: 0.85, h: 0.62, margin: 0,
      align: 'center', fontFace: BODY, fontSize: 8, color: C.white, lineSpacing: 10 });
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: 0.6, y: 3.2, w: 10.5, h: 0.42, rectRadius: 0.06,
    fill: { color: 'DDE5DF' } });
  s.addText('20 s of slots', { x: 0.6, y: 3.2, w: 10.5, h: 0.42, margin: 0,
    align: 'center', valign: 'middle', fontFace: BODY, fontSize: 11,
    bold: true, color: C.muted });
  s.addShape(pres.ShapeType.roundRect, {
    x: 11.2, y: 3.2, w: 1.5, h: 0.42, rectRadius: 0.06,
    fill: { color: C.forest2 } });
  s.addText('40 s asleep', { x: 11.2, y: 3.2, w: 1.5, h: 0.42, margin: 0,
    align: 'center', valign: 'middle', fontFace: BODY, fontSize: 10,
    bold: true, color: C.white });

  card(s, 0.6, 4.05, 5.9, 2.6, C.white);
  s.addText('Why three beacon slots?', { x: 0.95, y: 4.3, w: 5.2, h: 0.38, margin: 0,
    fontFace: HEAD, fontSize: 17, bold: true, color: C.ink });
  s.addText('Cluster B is out of gateway range by design — so it cannot hear the ' +
    'sync beacon either. An unsynchronised node drifts until it transmits into ' +
    'someone else’s slot, reintroducing exactly the collisions TDMA prevents.\n\n' +
    'Sync is relayed along the same tree the data takes. Each relay needs its OWN ' +
    'slot: two heads rebroadcasting in one slot would collide with each other.', {
    x: 0.95, y: 4.75, w: 5.2, h: 1.7, margin: 0,
    fontFace: BODY, fontSize: 11.5, color: C.muted, lineSpacing: 16 });

  card(s, 6.8, 4.05, 5.9, 2.6, C.forest2);
  s.addText('Duty cycle by role', { x: 7.15, y: 4.3, w: 5.2, h: 0.38, margin: 0,
    fontFace: HEAD, fontSize: 17, bold: true, color: C.white });
  const duty = [['member node', '6.7%'], ['backup head', '10.0%'],
                ['CH-B', '16.7%'], ['CH-A', '30.0%']];
  duty.forEach((d, i) => {
    const y = 4.82 + i * 0.42;
    s.addText(d[0], { x: 7.15, y, w: 2.6, h: 0.32, margin: 0,
      fontFace: BODY, fontSize: 12, color: C.moss2 });
    const bw = 1.9 * (parseFloat(d[1]) / 30);
    s.addShape(pres.ShapeType.roundRect, {
      x: 9.85, y: y + 0.07, w: Math.max(0.12, bw), h: 0.18, rectRadius: 0.04,
      fill: { color: i === 3 ? C.ember : C.moss } });
    s.addText(d[1], { x: 11.85, y, w: 0.75, h: 0.32, margin: 0, align: 'right',
      fontFace: BODY, fontSize: 12, bold: true, color: C.white });
  });

  s.addNotes('Ten slots, twenty seconds of activity, forty asleep. If asked why ' +
    'the beacon needs relaying: cluster B cannot hear the gateway, so it cannot ' +
    'hear the gateway’s clock either. Sync follows the data tree.');
}

// =========================================================================
// 9 — LOCAL MINIMA, THE PROBLEM
// =========================================================================
{
  const s = lightSlide();
  kicker(s, 'Design decision 2', C.sky);
  title(s, 'The local minimum, with real numbers');

  s.addText('Greedy geographic routing forwards to whichever neighbour is closest ' +
    'to the gateway. It needs no routing tables. It also fails.', {
    x: 0.6, y: 1.4, w: 7.6, h: 0.45, margin: 0,
    fontFace: BODY, fontSize: 14, color: C.ink });

  card(s, 0.6, 2.0, 7.0, 3.35, C.white);
  s.addText('Node N-03 is 714 m from the gateway. Its neighbours:', {
    x: 0.95, y: 2.28, w: 6.3, h: 0.32, margin: 0,
    fontFace: BODY, fontSize: 12.5, bold: true, color: C.ink });

  const nbrs = [['N-11', 726], ['N-16', 889], ['N-21', 1025], ['N-24', 1101], ['N-29', 1214]];
  nbrs.forEach((n, i) => {
    const y = 2.75 + i * 0.44;
    s.addText(n[0], { x: 1.0, y, w: 1.0, h: 0.32, margin: 0,
      fontFace: BODY, fontSize: 12, color: C.muted });
    const bw = 4.0 * (n[1] / 1214);
    s.addShape(pres.ShapeType.roundRect, {
      x: 2.05, y: y + 0.07, w: bw, h: 0.19, rectRadius: 0.04,
      fill: { color: 'D6DED8' } });
    s.addText(n[1] + ' m', { x: 6.15, y, w: 1.1, h: 0.32, margin: 0, align: 'right',
      fontFace: BODY, fontSize: 12, bold: true, color: C.alert });
  });

  s.addShape(pres.ShapeType.line, {
    x: 2.05, y: 2.7, w: 0, h: 2.35, line: { color: C.ember, width: 2, dashType: 'dash' } });
  s.addText('714 m — where we are', { x: 1.4, y: 5.02, w: 3.0, h: 0.3, margin: 0,
    fontFace: BODY, fontSize: 10.5, bold: true, color: C.ember });

  card(s, 8.0, 2.0, 4.7, 4.6, C.forest2);
  s.addText('Every neighbour is farther away.', {
    x: 8.35, y: 2.3, w: 4.0, h: 0.75, margin: 0,
    fontFace: HEAD, fontSize: 20, bold: true, color: C.white, lineSpacing: 25 });
  s.addText('Greedy has nothing to pick. The packet is stuck — and a perfectly good ' +
    'route exists:', { x: 8.35, y: 3.2, w: 4.0, h: 0.75, margin: 0,
    fontFace: BODY, fontSize: 12.5, color: C.moss2, lineSpacing: 18 });
  s.addText('N-03  →  N-21  →  N-08  →  GW', {
    x: 8.35, y: 4.0, w: 4.0, h: 0.4, margin: 0,
    fontFace: BODY, fontSize: 13.5, bold: true, color: C.ember });
  s.addText('That first hop goes 311 m in the WRONG direction. Greedy will never ' +
    'choose it, because greedy only ever moves closer.', {
    x: 8.35, y: 4.55, w: 4.0, h: 0.95, margin: 0,
    fontFace: BODY, fontSize: 12.5, color: C.white, lineSpacing: 18 });
  s.addText('Escaping a void requires moving away from the destination first.', {
    x: 8.35, y: 5.7, w: 4.0, h: 0.65, margin: 0,
    fontFace: BODY, fontSize: 11.5, italic: true, color: C.moss, lineSpacing: 16 });

  s.addNotes('Real numbers from the 50-node simulator. N-03 at 714 m, every ' +
    'neighbour farther. The working route starts by going 311 m backwards, which ' +
    'greedy cannot do by definition.');
}

// =========================================================================
// 10 — THE ANSWER, AT TWO SCALES
// =========================================================================
{
  const s = lightSlide();
  kicker(s, 'The central finding', C.ember);
  title(s, 'Protocol choice is scale-dependent');

  s.addText('The right answers at six nodes are the OPPOSITE of the right answers ' +
    'at fifty. Both were built, so the trade is measured rather than assumed.', {
    x: 0.6, y: 1.4, w: 11.5, h: 0.45, margin: 0,
    fontFace: BODY, fontSize: 14.5, italic: true, color: C.muted });

  const scales = [
    ['6 nodes — the hardware', C.forest2, C.moss2, [
      ['Channel access', 'Static TDMA slots'],
      ['Routing', 'Explicit next-hop tables'],
      ['Roles', 'Fixed, surveyed in advance'],
      ['Local minima', 'Cannot arise — no greedy decision'],
      ['Why', 'Five nodes cannot usefully negotiate. A fixed map is verifiable before deployment and cannot diverge.']
    ]],
    ['50 nodes — the simulator', '2E5C7A', 'A8CBE0', [
      ['Channel access', 'Contention-based'],
      ['Routing', 'Greedy geographic + perimeter'],
      ['Roles', 'Elected, rotated by residual energy'],
      ['Local minima', 'Occur, and must be recovered from'],
      ['Why', 'Static slots do not scale. Most nodes are out of sink range, so geographic forwarding earns its keep.']
    ]]
  ];

  scales.forEach((sc, i) => {
    const x = 0.6 + i * 6.15;
    card(s, x, 2.05, 5.85, 4.55, sc[1]);
    s.addText(sc[0], { x: x + 0.4, y: 2.3, w: 5.0, h: 0.4, margin: 0,
      fontFace: HEAD, fontSize: 19, bold: true, color: C.white });
    sc[3].forEach((r, j) => {
      const y = 2.9 + j * 0.6;
      if (j < 4) {
        s.addText(r[0], { x: x + 0.4, y, w: 1.9, h: 0.32, margin: 0,
          fontFace: BODY, fontSize: 10.5, color: sc[2] });
        s.addText(r[1], { x: x + 2.35, y, w: 3.15, h: 0.42, margin: 0,
          fontFace: BODY, fontSize: 11.5, bold: true, color: C.white, lineSpacing: 15 });
      } else {
        s.addText(r[1], { x: x + 0.4, y: y + 0.15, w: 5.1, h: 0.9, margin: 0,
          fontFace: BODY, fontSize: 11.5, italic: true, color: sc[2], lineSpacing: 16 });
      }
    });
  });

  s.addNotes('If asked why the two halves of the project use contradictory ' +
    'techniques — this slide is the answer. It is not inconsistency, it is the ' +
    'finding. Contention and greedy routing are correct at scale and actively ' +
    'harmful at six nodes.');
}

// =========================================================================
// 11 — ENERGY
// =========================================================================
{
  const s = lightSlide();
  kicker(s, 'Energy');
  title(s, 'The standard model was wrong for this hardware');

  card(s, 0.6, 1.5, 5.9, 2.35, C.white);
  s.addText('What went wrong', { x: 0.95, y: 1.75, w: 5.2, h: 0.35, margin: 0,
    fontFace: HEAD, fontSize: 17, bold: true, color: C.ink });
  s.addText('The LEACH first-order radio model gave ZERO node deaths in 400 rounds. ' +
    'It was formulated for motes — microcontrollers where the radio dominates and a ' +
    'packet costs microjoules. Per transmission it predicted ~2.4 mJ against a 12 kJ ' +
    'battery: depletion would take millions of rounds.', {
    x: 0.95, y: 2.15, w: 5.2, h: 1.5, margin: 0,
    fontFace: BODY, fontSize: 12, color: C.muted, lineSpacing: 17 });

  card(s, 0.6, 4.05, 5.9, 2.6, C.forest2);
  s.addText('The correction', { x: 0.95, y: 4.3, w: 5.2, h: 0.35, margin: 0,
    fontFace: HEAD, fontSize: 17, bold: true, color: C.white });
  s.addText('A Raspberry Pi 4 draws ~2.1 W awake against milliwatts of radio. The SoC ' +
    'dominates entirely. Adding a platform baseline term made the model behave.', {
    x: 0.95, y: 4.7, w: 5.2, h: 0.85, margin: 0,
    fontFace: BODY, fontSize: 12, color: C.moss2, lineSpacing: 17 });
  s.addText('Battery life is set by how long a node stays AWAKE — not by transmit ' +
    'power or packet size.', {
    x: 0.95, y: 5.62, w: 5.2, h: 0.8, margin: 0,
    fontFace: BODY, fontSize: 13, bold: true, color: C.ember, lineSpacing: 19 });

  s.addText('Rotating the cluster-head role', {
    x: 6.9, y: 1.5, w: 5.8, h: 0.4, margin: 0,
    fontFace: HEAD, fontSize: 19, bold: true, color: C.ink });

  s.addChart(pres.ChartType.bar, [
    { name: 'Rotation ON', labels: ['First node death (round)'], values: [307] },
    { name: 'Rotation OFF', labels: ['First node death (round)'], values: [212] }
  ], {
    x: 6.9, y: 1.95, w: 5.8, h: 2.0,
    barDir: 'bar', barGrouping: 'clustered',
    chartColors: [C.moss, 'C6CFC8'],
    showValue: true, dataLabelPosition: 'outEnd',
    dataLabelColor: C.ink, dataLabelFontFace: BODY, dataLabelFontSize: 12,
    showLegend: true, legendPos: 'b', legendFontSize: 10, legendColor: C.muted,
    catAxisLabelColor: C.muted, catAxisLabelFontSize: 10,
    valAxisLabelColor: C.muted, valAxisLabelFontSize: 9,
    valGridLine: { color: 'E4EAE5', size: 1 }, catGridLine: { style: 'none' },
    valAxisMaxVal: 350
  });

  card(s, 6.9, 4.2, 5.8, 2.45, C.white);
  s.addText('Rotation delays the first death by 45%', {
    x: 7.25, y: 4.45, w: 5.1, h: 0.4, margin: 0,
    fontFace: BODY, fontSize: 14, bold: true, color: C.ink });
  s.addText('Head-duty spread also falls sharply — σ 28.4 with rotation against 72.6 ' +
    'without.\n\nBut half-network death barely moves: round 317 versus 320. ' +
    'Rotation EQUALISES node lifetime rather than extending total network life — ' +
    'which is precisely its purpose, and a more informative result than a uniform ' +
    'improvement would have been.', {
    x: 7.25, y: 4.9, w: 5.1, h: 1.6, margin: 0,
    fontFace: BODY, fontSize: 11.5, color: C.muted, lineSpacing: 16 });

  s.addNotes('Two things here. The energy model correction — a Pi is not a mote, ' +
    'and duty cycling is the whole story. And the rotation result, where the ' +
    'interesting part is what it does NOT do: total network lifetime is unchanged.');
}

// =========================================================================
// 12 — RESILIENCE
// =========================================================================
{
  const s = darkSlide();
  kicker(s, 'Resilience', C.moss2);
  title(s, 'What happens when something breaks', { color: C.white });

  const items = [
    ['Link fails', 'Acknowledged forwarding, three retries, then fall through to the next hop in the ordered route table.', C.moss],
    ['Cluster head dies', 'Its backup detects unacknowledged uplinks and promotes itself — adopting the dead head’s slots, which are free precisely because it is silent.', C.ember],
    ['No route at all', 'Store and forward. The reading is buffered, not discarded, and retried next frame.', C.sky],
    ['Operator needs it stopped', 'A command field in the beacon — the network’s only downlink. Halt auto-expires after 30 minutes.', 'B07AA8']
  ];
  items.forEach((it, i) => {
    const x = 0.7 + (i % 2) * 6.15, y = 1.6 + Math.floor(i / 2) * 2.55;
    card(s, x, y, 5.85, 2.25, C.forest2);
    nodeDot(s, x + 0.4, y + 0.4, 0.5, it[2], String(i + 1), C.white);
    s.addText(it[0], { x: x + 1.1, y: y + 0.42, w: 4.4, h: 0.4, margin: 0,
      fontFace: HEAD, fontSize: 18, bold: true, color: C.white });
    s.addText(it[1], { x: x + 0.4, y: y + 1.1, w: 5.1, h: 0.95, margin: 0,
      fontFace: BODY, fontSize: 12, color: C.moss2, lineSpacing: 17 });
  });

  s.addText('A halted fire-detection network is a silent one — so the stop command ' +
    'has a dead-man timer at both ends.', {
    x: 0.7, y: 6.72, w: 11.9, h: 0.4, margin: 0, align: 'center',
    fontFace: BODY, fontSize: 12, italic: true, color: C.moss });

  s.addNotes('Four failure modes and the response to each. The halt auto-expiry is ' +
    'worth calling out as a safety property — a latched stop that someone forgets ' +
    'to clear is worse than the problem it solves.');
}

// =========================================================================
// 13 — VERIFICATION
// =========================================================================
{
  const s = lightSlide();
  kicker(s, 'Verification', C.ember);
  title(s, 'Testing that actually found things');

  card(s, 0.6, 1.5, 5.6, 2.15, C.forest2);
  s.addText('The deployment firmware, on a virtual radio', {
    x: 0.95, y: 1.78, w: 4.9, h: 0.65, margin: 0,
    fontFace: HEAD, fontSize: 17, bold: true, color: C.white, lineSpacing: 22 });
  s.addText('netsim.py imports the actual gateway.py and sensor_node.py rather than ' +
    'reimplementing them, and runs all six stacks against a channel that models ' +
    'airtime, range and overlap. What is tested is what ships.', {
    x: 0.95, y: 2.5, w: 4.9, h: 1.0, margin: 0,
    fontFace: BODY, fontSize: 11.5, color: C.moss2, lineSpacing: 16 });

  const defects = [
    ['Truth test on a hop address', 'The gateway’s address is 0 — falsy in Python. Every successful delivery read as failure; heads re-queued delivered readings forever.'],
    ['No beacon relay', 'Cluster B, out of gateway range by design, could never synchronise.'],
    ['Listen slots hand-listed', 'CH-A slept through CH-B’s transmission slot. Its readings had nowhere to land.'],
    ['Failover on beacon silence', 'A backup seized the slots of a working head — 48 collisions.'],
    ['Halt severed the relay', 'A halted CH-A stopped relaying the beacon, cutting cluster B off from the command channel entirely.']
  ];
  defects.forEach((d, i) => {
    const y = 1.5 + i * 1.06;
    card(s, 6.55, y, 6.15, 0.92, C.white);
    nodeDot(s, 6.78, y + 0.26, 0.4, C.alert, String(i + 1), C.white);
    s.addText(d[0], { x: 7.32, y: y + 0.08, w: 5.15, h: 0.28, margin: 0,
      fontFace: BODY, fontSize: 12, bold: true, color: C.ink });
    s.addText(d[1], { x: 7.32, y: y + 0.36, w: 5.15, h: 0.5, margin: 0,
      fontFace: BODY, fontSize: 10, color: C.muted, lineSpacing: 13 });
  });

  s.addText('5 defects', { x: 0.6, y: 3.95, w: 5.6, h: 0.7, margin: 0,
    fontFace: HEAD, fontSize: 42, bold: true, color: C.ember });
  s.addText('found before any hardware was connected', {
    x: 0.6, y: 4.62, w: 5.6, h: 0.35, margin: 0,
    fontFace: BODY, fontSize: 12.5, color: C.muted });
  s.addText('Two would have presented on hardware as intermittent data loss — the ' +
    'hardest class of fault to diagnose in a forest. One would have appeared only ' +
    'under head failure, which is exactly the condition the mechanism exists to handle.',
    { x: 0.6, y: 5.15, w: 5.6, h: 1.3, margin: 0,
      fontFace: BODY, fontSize: 12, color: C.ink, lineSpacing: 18 });

  s.addNotes('The strongest slide in the deck. Most projects claim testing; this ' +
    'shows testing that found something. Be ready to walk through the falsy-zero ' +
    'bug in detail — it is the most instructive.');
}

// =========================================================================
// 14 — RESULTS
// =========================================================================
{
  const s = darkSlide();
  kicker(s, 'Results — simulated', C.moss2);
  title(s, 'Eight frames, six nodes', { color: C.white });

  const stats = [
    ['0', 'collisions in 184\ntransmissions', C.ember],
    ['100%', 'packet delivery,\n40 of 40 readings', C.moss2],
    ['0', 'duplicates and\n0 retries', C.moss2],
    ['3', 'hops for node 5,\nvia CH-B and CH-A', C.moss2]
  ];
  stats.forEach((st, i) => {
    const x = 0.7 + i * 3.1;
    card(s, x, 1.55, 2.85, 2.0, C.forest2);
    s.addText(st[0], { x: x + 0.3, y: 1.8, w: 2.25, h: 0.85, margin: 0,
      fontFace: HEAD, fontSize: 42, bold: true, color: st[2] });
    s.addText(st[1], { x: x + 0.3, y: 2.68, w: 2.3, h: 0.7, margin: 0,
      fontFace: BODY, fontSize: 11.5, color: C.moss, lineSpacing: 15 });
  });

  s.addText('Delivery path per node', { x: 0.7, y: 3.85, w: 6.0, h: 0.4, margin: 0,
    fontFace: HEAD, fontSize: 18, bold: true, color: C.white });

  const paths = [
    ['CH-A', '1 → GW', 1], ['node 2', '2 → 1 → GW', 2], ['node 3', '3 → 1 → GW', 2],
    ['CH-B', '4 → 1 → GW', 2], ['node 5', '5 → 4 → 1 → GW', 3]
  ];
  paths.forEach((p, i) => {
    const y = 4.4 + i * 0.52;
    s.addText(p[0], { x: 0.7, y, w: 1.3, h: 0.32, margin: 0,
      fontFace: BODY, fontSize: 12, color: C.moss2 });
    s.addText(p[1], { x: 2.1, y, w: 2.6, h: 0.32, margin: 0,
      fontFace: BODY, fontSize: 12, bold: true, color: C.white });
    for (let h = 0; h < p[2]; h++) {
      nodeDot(s, 4.85 + h * 0.42, y + 0.05, 0.22, h === p[2] - 1 ? C.ember : C.sky);
    }
    s.addText(p[2] + (p[2] === 1 ? ' hop' : ' hops'),
      { x: 6.3, y, w: 0.9, h: 0.32, margin: 0,
        fontFace: BODY, fontSize: 11, color: C.moss });
  });

  card(s, 7.6, 3.8, 5.0, 3.05, C.forest2);
  s.addText('Fault injection', { x: 7.95, y: 4.1, w: 4.3, h: 0.38, margin: 0,
    fontFace: HEAD, fontSize: 17, bold: true, color: C.white });
  bullets(s, [
    'Fire at node 5 propagated across all three hops, flagged correctly, zero collisions',
    'CH-A killed: node 3 promoted after three unacknowledged frames, adopted its slots, zero collisions through failover',
    'Cluster B correctly partitioned and buffered — no algorithm can cross a physical partition'
  ], 7.95, 4.5, 4.3, 2.2, 11, C.moss2);

  s.addNotes('Headline numbers. If asked about the partition case — that is correct ' +
    'behaviour, not a failure. CH-B can only hear CH-A and node 5, so when CH-A dies ' +
    'cluster B is physically cut off. Buffering is the right answer.');
}

// =========================================================================
// 15 — DEMO
// =========================================================================
{
  const s = lightSlide();
  kicker(s, 'Live demonstration');
  title(s, 'What I will show you');

  const steps = [
    ['Topology on the map', 'Cluster heads ringed in blue, backups dashed, members linked to their head. The gateway at the centre.'],
    ['A three-hop route', 'Select node 5 — its path draws as 5 → CH-B → CH-A → gateway, coloured per hop.'],
    ['Local minima recovery', 'On the 50-node view, a node outlined amber. Its route detours around the ridge in perimeter mode.'],
    ['Cluster head failover', 'Kill a head. Within three rounds its backup is promoted, the cluster re-homes, the event log records it.'],
    ['Emergency stop', 'Halt the network. The banner goes red and every node stops transmitting — then auto-resumes.']
  ];
  steps.forEach((st, i) => {
    const y = 1.5 + i * 1.05;
    card(s, 0.6, y, 7.5, 0.92);
    nodeDot(s, 0.88, y + 0.24, 0.44, C.forest2, String(i + 1), C.white);
    s.addText(st[0], { x: 1.48, y: y + 0.09, w: 6.4, h: 0.3, margin: 0,
      fontFace: BODY, fontSize: 13, bold: true, color: C.ink });
    s.addText(st[1], { x: 1.48, y: y + 0.39, w: 6.4, h: 0.45, margin: 0,
      fontFace: BODY, fontSize: 10.5, color: C.muted, lineSpacing: 14 });
  });

  card(s, 8.45, 1.5, 4.25, 5.2, C.forest2);
  s.addText('Two views, one dashboard', { x: 8.8, y: 1.8, w: 3.55, h: 0.75, margin: 0,
    fontFace: HEAD, fontSize: 18, bold: true, color: C.white, lineSpacing: 23 });
  s.addText('A single interface abstracts the data origin, so the same rendering code ' +
    'serves the simulator and the live gateway. Switching between them is one line ' +
    'in the markup.', { x: 8.8, y: 2.65, w: 3.55, h: 1.2, margin: 0,
    fontFace: BODY, fontSize: 12, color: C.moss2, lineSpacing: 17 });

  s.addText('SIMULATED', { x: 8.8, y: 4.0, w: 3.55, h: 0.28, margin: 0,
    fontFace: BODY, fontSize: 10, bold: true, charSpacing: 1.6, color: C.moss });
  s.addText('50 nodes, clustering and geographic routing at a scale where they earn ' +
    'their keep', { x: 8.8, y: 4.28, w: 3.55, h: 0.7, margin: 0,
    fontFace: BODY, fontSize: 11, color: C.white, lineSpacing: 15 });

  s.addText('LIVE', { x: 8.8, y: 5.1, w: 3.55, h: 0.28, margin: 0,
    fontFace: BODY, fontSize: 10, bold: true, charSpacing: 1.6, color: C.moss });
  s.addText('Real telemetry from the gateway Pi, with the true multi-hop path drawn ' +
    'from the packets themselves', { x: 8.8, y: 5.38, w: 3.55, h: 0.8, margin: 0,
    fontFace: BODY, fontSize: 11, color: C.white, lineSpacing: 15 });

  s.addNotes('Demo running order. Have the dashboard already open before starting. ' +
    'The failover and the halt are the two that always land — they are visibly ' +
    'dynamic rather than static screenshots.');
}

// =========================================================================
// 16 — CLOSE
// =========================================================================
{
  const s = darkSlide();

  const pts = [[0.9,5.4],[1.9,6.0],[2.9,5.5],[3.9,6.2],[4.9,5.7]];
  pts.forEach((p, i) => nodeDot(s, p[0], p[1], i === 2 ? 0.34 : 0.24,
                               i === 2 ? C.ember : C.moss));

  kicker(s, 'In closing', C.moss2);
  s.addText('Two claims I can defend', {
    x: 0.7, y: 1.15, w: 11.5, h: 0.85, margin: 0,
    fontFace: HEAD, fontSize: 36, bold: true, color: C.white });

  const claims = [
    ['Collisions and routing dead ends are eliminated by construction',
     'TDMA slots make overlap structurally impossible; explicit routing tables leave no geometric decision that could stall. Neither is a retry mechanism.'],
    ['Protocol choice is scale-dependent, and I measured it',
     'The correct techniques at six nodes are the opposite of those at fifty. Building both makes that a finding rather than an assumption.']
  ];
  claims.forEach((c, i) => {
    const y = 2.3 + i * 1.5;
    nodeDot(s, 0.7, y + 0.12, 0.48, i === 0 ? C.ember : C.sky, String(i + 1), C.white);
    s.addText(c[0], { x: 1.45, y, w: 10.9, h: 0.45, margin: 0,
      fontFace: HEAD, fontSize: 19, bold: true, color: C.white });
    s.addText(c[1], { x: 1.45, y: y + 0.5, w: 10.9, h: 0.8, margin: 0,
      fontFace: BODY, fontSize: 13, color: C.moss2, lineSpacing: 19 });
  });

  s.addText('Next', { x: 6.6, y: 5.35, w: 5.9, h: 0.3, margin: 0,
    fontFace: BODY, fontSize: 10.5, bold: true, charSpacing: 1.8, color: C.moss });
  s.addText('Microcontroller sensor nodes to escape the Pi’s 0.35 W sleep floor  ·  ' +
    'solar harvesting  ·  adaptive frame length  ·  sensor fusion across neighbours  ·  ' +
    'alert integration with forest department', {
    x: 6.6, y: 5.65, w: 5.9, h: 1.1, margin: 0,
    fontFace: BODY, fontSize: 11.5, color: C.moss2, lineSpacing: 16 });

  s.addText('github.com/nishankswamy/forest-fire-dashboard', {
    x: 0.7, y: 6.85, w: 6.5, h: 0.35, margin: 0,
    fontFace: BODY, fontSize: 11.5, color: C.moss });

  s.addNotes('Close on the two claims. Then invite questions — the areas I expect ' +
    'are why TDMA over CSMA, why the two halves differ, and whether the simulator ' +
    'results transfer to hardware. Honest answer to the last one: they verify logic, ' +
    'not radio behaviour.');
}

pres.writeFile({ fileName: 'forest-fire-project.pptx' })
  .then(f => console.log('written:', f));
