import EvalDashboard from "@/components/eval/EvalDashboard";

export const metadata = {
  title: "Evaluation - Multimodal RAG",
};

export default function EvaluationPage() {
  return (
    <main className="h-full overflow-y-auto">
      <EvalDashboard />
    </main>
  );
}
