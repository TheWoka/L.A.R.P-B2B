import { useParams } from "react-router-dom";

import ReportView from "../components/ReportView";

export default function ReportPage() {
  const { reportId } = useParams<{ reportId: string }>();
  return <ReportView key={reportId} reportId={reportId} />;
}
