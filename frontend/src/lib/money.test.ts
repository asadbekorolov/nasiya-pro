import { describe, expect, it } from "vitest";

import {
  formatMoney,
  formatQuantity,
  fromCents,
  isPositiveMoneyInput,
  lineTotalCents,
  normalizeMoneyInput,
  toCents,
} from "./money";

describe("money", () => {
  it("parses and prints without floats", () => {
    expect(toCents("1250000.50")).toBe(125000050n);
    expect(toCents("-80")).toBe(-8000n);
    expect(fromCents(5n)).toBe("0.05");
    expect(fromCents(-125000050n)).toBe("-1250000.50");
    // a classic float trap
    expect(fromCents(toCents("0.1") + toCents("0.2"))).toBe("0.30");
  });

  it("computes line totals like the backend (ROUND_HALF_UP)", () => {
    expect(fromCents(lineTotalCents("12500.50", "3"))).toBe("37501.50");
    expect(fromCents(lineTotalCents("0.10", "0.005"))).toBe("0.00"); // 0.0005 -> 0.00
    expect(fromCents(lineTotalCents("0.10", "0.050"))).toBe("0.01"); // 0.005 -> 0.01
    expect(fromCents(lineTotalCents("9999999999999.99", "1"))).toBe("9999999999999.99");
  });

  it("formats for people", () => {
    expect(formatMoney("1250000.00")).toBe("1 250 000 so'm");
    expect(formatMoney("1200.50")).toBe("1 200,50 so'm");
    expect(formatMoney("-80.00")).toBe("−80 so'm");
    expect(formatQuantity("12.500")).toBe("12,5");
    expect(formatQuantity("1000.000")).toBe("1 000");
  });

  it("validates user input", () => {
    expect(isPositiveMoneyInput("1 200 000")).toBe(true);
    expect(isPositiveMoneyInput("1200,5")).toBe(true);
    expect(isPositiveMoneyInput("0")).toBe(false);
    expect(isPositiveMoneyInput("-5")).toBe(false);
    expect(isPositiveMoneyInput("1.234")).toBe(false);
    expect(normalizeMoneyInput("1 200 000,5")).toBe("1200000.50");
  });
});
