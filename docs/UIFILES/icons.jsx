// icons.jsx — small SVG icon set
const Icon = ({ d, size = 16, stroke = "currentColor", fill = "none", strokeWidth = 1.6 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill={fill} stroke={stroke}
       strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
    {Array.isArray(d) ? d.map((p, i) => <path key={i} d={p} />) : <path d={d} />}
  </svg>
);

const IconDevices = (p) => <Icon {...p} d={["M4 6h16v10H4z", "M9 20h6", "M12 16v4"]} />;
const IconMouse = (p) => <Icon {...p} d={["M12 3a6 6 0 0 0-6 6v6a6 6 0 0 0 12 0V9a6 6 0 0 0-6-6z", "M12 3v6"]} />;
const IconEdges = (p) => <Icon {...p} d={["M3 7h7v10H3z", "M14 9h7v6h-7z", "M10 12h4", "M12 10l2 2-2 2"]} />;
const IconClip = (p) => <Icon {...p} d={["M9 4h6v3H9z", "M7 6H5v15h14V6h-2", "M9 12h6", "M9 16h4"]} />;
const IconShield = (p) => <Icon {...p} d={["M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z", "M9 12l2 2 4-4"]} />;
const IconLogs = (p) => <Icon {...p} d={["M5 4h14v16H5z", "M8 9h8", "M8 13h8", "M8 17h5"]} />;

const IconMac = (p) => <Icon {...p} d={["M5 5h14v10H5z", "M3 19h18", "M10 15l-1 4", "M14 15l1 4"]} />;
const IconWin = (p) => <Icon {...p} d={["M3 5l8-1v8H3z", "M12 4l9-1v9h-9z", "M3 13h8v7l-8-1z", "M12 13h9v8l-9-1z"]} />;
const IconDeck = (p) => <Icon {...p} d={["M4 7h16v10H4z", "M8 12h2", "M14 11l1 1-1 1", "M16 11l1 1-1 1", "M8 5v2", "M16 5v2"]} />;
const IconChevron = (p) => <Icon {...p} d="M9 6l6 6-6 6" />;
const IconPlus = (p) => <Icon {...p} d={["M12 5v14", "M5 12h14"]} />;
const IconCheck = (p) => <Icon {...p} d="M5 12l5 5L20 7" stroke="white" strokeWidth={2.5} />;
const IconRefresh = (p) => <Icon {...p} d={["M4 12a8 8 0 0 1 14-5.3L20 4", "M20 4v5h-5", "M20 12a8 8 0 0 1-14 5.3L4 20", "M4 20v-5h5"]} />;
const IconClose = (p) => <Icon {...p} d={["M6 6l12 12", "M18 6l-12 12"]} />;
const IconStop = (p) => <Icon {...p} d={["M12 3l9 5v8l-9 5-9-5V8z", "M9 9h6v6H9z"]} fill="currentColor" stroke="none" />;
const IconKey = (p) => <Icon {...p} d={["M14 9a4 4 0 1 0-3.5 4l1 1 1.5-1.5L14 14l2-2-1-1z", "M3 21l7-7"]} />;
const IconQR = (p) => <Icon {...p} d={["M4 4h6v6H4z", "M14 4h6v6h-6z", "M4 14h6v6H4z", "M14 14h2v2h-2z", "M18 14h2v2h-2z", "M14 18h2v2h-2z", "M18 18h2v2h-2z"]} />;

window.Icon = Icon;
window.Icons = {
  Devices: IconDevices, Mouse: IconMouse, Edges: IconEdges, Clip: IconClip,
  Shield: IconShield, Logs: IconLogs, Mac: IconMac, Win: IconWin, Deck: IconDeck,
  Chevron: IconChevron, Plus: IconPlus, Check: IconCheck, Refresh: IconRefresh,
  Close: IconClose, Stop: IconStop, Key: IconKey, QR: IconQR,
};
