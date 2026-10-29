import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import StatisticModule from "antd/lib/statistic/index.js";

import { roundPercentage } from "../src/lib/metrics.ts";

const Statistic = StatisticModule.default;

test("rounds saved audit metrics before Statistic formats the headline", () => {
  for (const [fraction, expected] of [[0.9925, "99.3%"], [0.9279, "92.8%"], [0.9167, "91.7%"]]) {
    const html = renderToStaticMarkup(createElement(Statistic, {
      value: roundPercentage(fraction), precision: 1, suffix: "%",
    }));
    assert.equal(html.replace(/<[^>]*>/g, ""), expected);
  }
});

test("preserves endpoints and rounds half-tenth boundaries upwards", () => {
  for (const [fraction, expected] of [[0, 0], [1, 100], [0.0005, 0.1], [0.9005, 90.1], [0.9995, 100]]) {
    assert.equal(roundPercentage(fraction), expected);
  }
});
