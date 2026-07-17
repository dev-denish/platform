import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { classColor } from "../lib/colors.js";
import { formatNumber } from "../lib/format.js";

/** data: [{ name, value }] - value in hectares. */
export default function LandCoverPie({ data }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <PieChart>
        <Pie
          data={data}
          dataKey="value"
          nameKey="name"
          cx="50%"
          cy="50%"
          innerRadius={62}
          outerRadius={104}
          paddingAngle={1.5}
          stroke="none"
        >
          {data.map((d) => (
            <Cell key={d.name} fill={classColor(d.name)} />
          ))}
        </Pie>
        <Tooltip
          formatter={(value) => [`${formatNumber(value)} ha`, ""]}
          contentStyle={{
            background: "#ffffff",
            border: "1px solid #e5ece8",
            borderRadius: 8,
            fontSize: 12,
            boxShadow: "0 4px 16px rgba(9,38,26,0.12)",
          }}
          itemStyle={{ color: "#101f1a", fontFamily: "IBM Plex Mono, monospace" }}
          labelStyle={{ color: "#4c5d57" }}
        />
      </PieChart>
    </ResponsiveContainer>
  );
}
