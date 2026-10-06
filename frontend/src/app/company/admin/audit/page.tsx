import InsightsPage, { type InsightsParams } from "../../../admin/insights-page";

export const metadata = { title: "Audit Logs | Mikan" };
export default function Page({ searchParams }: { searchParams: InsightsParams }) {
  return <InsightsPage company audit searchParams={searchParams} />;
}