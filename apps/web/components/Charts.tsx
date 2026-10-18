"use client";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const COLORS = ["#2a7f9e", "#d9822b", "#7b61b5", "#3a9d6e"];
export function SimpleBar({ data, x, y, label }: { data: any[]; x: string; y: string; label: string }) { // eslint-disable-line @typescript-eslint/no-explicit-any
  return (
    <div role="img" aria-label={label} style={{ height: 260 }}>
      <ResponsiveContainer><BarChart data={data}><CartesianGrid strokeDasharray="3 3" opacity={0.3} /><XAxis dataKey={x} fontSize={11} /><YAxis fontSize={11} /><Tooltip /><Bar dataKey={y} fill={COLORS[0]} /></BarChart></ResponsiveContainer>
    </div>
  );
}
export function MultiLine({ data, x, ys, label }: { data: any[]; x: string; ys: string[]; label: string }) { // eslint-disable-line @typescript-eslint/no-explicit-any
  return (
    <div role="img" aria-label={label} style={{ height: 280 }}>
      <ResponsiveContainer><LineChart data={data}><CartesianGrid strokeDasharray="3 3" opacity={0.3} /><XAxis dataKey={x} fontSize={11} /><YAxis fontSize={11} /><Tooltip /><Legend />
        {ys.map((k, i) => <Line key={k} type="monotone" dataKey={k} stroke={COLORS[i % COLORS.length]} dot={false} strokeWidth={2} />)}</LineChart></ResponsiveContainer>
    </div>
  );
}
