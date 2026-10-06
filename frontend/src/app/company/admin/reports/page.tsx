import InsightsPage, { type InsightsParams } from "../../../admin/insights-page";

export const metadata = { title: "Reports | Mikan" };
export default function Page({ searchParams }: { searchParams: InsightsParams }) {
  return <InsightsPage company audit={false} searchParams={searchParams} />;
}