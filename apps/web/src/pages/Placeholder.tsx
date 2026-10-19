import { useParams } from "react-router-dom";

export function Placeholder({ title, milestone }: { title: string; milestone: string }) {
  const { client } = useParams();
  return (
    <section className="panel p-6">
      <h1 className="text-lg font-semibold">{title}{client ? ` · ${client}` : ""}</h1>
      <p className="muted mt-2">This screen ships in {milestone}.</p>
    </section>
  );
}
