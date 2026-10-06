import InsightsPage, { type InsightsParams } from "../insights-page";

export const metadata = { title: "Audit Logs | Mikan" };
export default function Page({ searchParams }: { searchParams: InsightsParams }) {
  return <InsightsPage company={false} audit searchParams={searchParams} />;
}