import * as m from "../locales/messages";

export const buildFixtures = [
  {
    id: "copper",
    title: "Copper Bolt 5×5",
    creator: "CircuitSage · 红石猫",
    dimensions: "9 × 7 × 4",
    image: "/fixtures/copper-door.svg",
    category: m.workbenchPistonDoor,
    timing: "8 gt",
  },
  {
    id: "iris",
    title: "Observerless Iris / 无侦测器全隐藏虹膜门",
    creator: "Space Builder",
    dimensions: "12 × 12 × 5",
    image: "/fixtures/iris-door.svg",
    category: m.workbenchSeamlessDoor,
    timing: "12 gt",
  },
  {
    id: "extender",
    title: "Slim Piston Extender",
    creator: "CircuitSage",
    dimensions: "3 × 5 × 8",
    image: null,
    category: m.workbenchExtender,
    timing: "—",
  },
] as const;
