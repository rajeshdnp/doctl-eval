/**
 * Custom SVG confusion matrix heatmap.
 * No external heatmap library — just an SVG grid with HSL color interpolation.
 *
 * Design: white (0) → deep indigo (max), row-normalized values drive color.
 * Diagonal cells (correct predictions) have a brighter border.
 * Clicking a non-diagonal cell fires onCellClick for filtering the disagreement table.
 */
import React from "react";

const LABELS = ["bug", "enhancement", "question", "documentation", "security", "other"];
const CELL_SIZE = 62;
const LABEL_WIDTH = 88;
const LABEL_HEIGHT = 20;
const FONT_SIZE = 10;

interface Props {
  title: string;
  normalized: Record<string, Record<string, number>>;
  raw: Record<string, Record<string, number>>;
  onCellClick?: (trueLabel: string, predLabel: string) => void;
}

function hslColor(value: number): string {
  // 0 → near-white (#f8f8ff), 1 → deep indigo (#3730a3)
  // Interpolate in hue 240 (blue), varying lightness from 95% to 25%
  const lightness = 95 - value * 70;
  const saturation = value < 0.05 ? 0 : 70;
  return `hsl(240, ${saturation}%, ${lightness}%)`;
}

function textColor(value: number): string {
  return value > 0.5 ? "#ffffff" : "#1f2937";
}

export function ConfusionMatrix({ title, normalized, raw, onCellClick }: Props) {
  const width = LABEL_WIDTH + LABELS.length * CELL_SIZE + 2;
  const height = LABEL_HEIGHT + LABELS.length * CELL_SIZE + 24 + 16;

  return (
    <div className="flex flex-col items-center">
      <h3 className="text-sm font-semibold text-gray-300 mb-2">{title}</h3>
      <div className="text-xs text-gray-500 mb-1 self-start ml-24">← Predicted →</div>
      <svg width={width} height={height} className="overflow-visible">
        {/* Column labels (predicted) */}
        {LABELS.map((label, j) => (
          <text
            key={`col-${label}`}
            x={LABEL_WIDTH + j * CELL_SIZE + CELL_SIZE / 2}
            y={LABEL_HEIGHT - 4}
            textAnchor="middle"
            fontSize={FONT_SIZE - 1}
            fill="#9ca3af"
            transform={`rotate(-30, ${LABEL_WIDTH + j * CELL_SIZE + CELL_SIZE / 2}, ${LABEL_HEIGHT - 4})`}
          >
            {label}
          </text>
        ))}

        {/* Row labels (true) and cells */}
        {LABELS.map((trueLabel, i) => (
          <React.Fragment key={`row-${trueLabel}`}>
            {/* Row label */}
            <text
              x={LABEL_WIDTH - 6}
              y={LABEL_HEIGHT + i * CELL_SIZE + CELL_SIZE / 2 + 4}
              textAnchor="end"
              fontSize={FONT_SIZE - 1}
              fill="#9ca3af"
            >
              {trueLabel}
            </text>

            {/* Cells */}
            {LABELS.map((predLabel, j) => {
              const normVal = normalized[trueLabel]?.[predLabel] ?? 0;
              const rawVal = raw[trueLabel]?.[predLabel] ?? 0;
              const isdiagonal = i === j;
              const x = LABEL_WIDTH + j * CELL_SIZE;
              const y = LABEL_HEIGHT + i * CELL_SIZE;

              return (
                <g
                  key={`cell-${i}-${j}`}
                  onClick={() => !isdiagonal && onCellClick?.(trueLabel, predLabel)}
                  style={{ cursor: isdiagonal ? "default" : "pointer" }}
                >
                  <rect
                    x={x + 1}
                    y={y + 1}
                    width={CELL_SIZE - 2}
                    height={CELL_SIZE - 2}
                    fill={hslColor(normVal)}
                    stroke={isdiagonal ? "#6366f1" : "#374151"}
                    strokeWidth={isdiagonal ? 2 : 0.5}
                    rx={2}
                  />
                  {rawVal > 0 && (
                    <>
                      <text
                        x={x + CELL_SIZE / 2}
                        y={y + CELL_SIZE / 2 - 4}
                        textAnchor="middle"
                        fontSize={11}
                        fontWeight={isdiagonal ? "bold" : "normal"}
                        fill={textColor(normVal)}
                      >
                        {rawVal}
                      </text>
                      <text
                        x={x + CELL_SIZE / 2}
                        y={y + CELL_SIZE / 2 + 9}
                        textAnchor="middle"
                        fontSize={9}
                        fill={textColor(normVal)}
                        opacity={0.8}
                      >
                        {(normVal * 100).toFixed(0)}%
                      </text>
                    </>
                  )}
                </g>
              );
            })}
          </React.Fragment>
        ))}

        {/* Axis labels */}
        <text
          x={LABEL_WIDTH / 2}
          y={LABEL_HEIGHT + LABELS.length * CELL_SIZE / 2}
          textAnchor="middle"
          fontSize={FONT_SIZE}
          fill="#6b7280"
          transform={`rotate(-90, ${LABEL_WIDTH / 2 - 16}, ${LABEL_HEIGHT + LABELS.length * CELL_SIZE / 2})`}
        >
          True Label ↓
        </text>
      </svg>
    </div>
  );
}
