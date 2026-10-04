export const MENU = [
  { id: "espresso", name: "Espresso", priceCents: 250 },
  { id: "latte", name: "Latte", priceCents: 400 },
];

export function cartTotalCents(lines) {
  return lines.reduce((sum, line) => {
    const item = MENU.find((m) => m.id === line.id);
    if (!item) throw new Error(`unknown menu item: ${line.id}`);
    return sum + item.priceCents * line.qty;
  }, 0);
}
