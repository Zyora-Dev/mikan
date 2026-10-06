import NotificationPage, { type NotificationParams } from "../../../notification-page";
export const metadata = { title: "Notifications | Mikan" };
export default function Page({ searchParams }: { searchParams: NotificationParams }) {
  return <NotificationPage scope="company" searchParams={searchParams} />;
}