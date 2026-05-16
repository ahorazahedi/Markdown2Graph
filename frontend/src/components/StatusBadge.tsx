import { Badge } from "@/components/ui/badge";

export function DocumentStatusBadge({ status }: { status: string }) {
  switch (status) {
    case "completed":  return <Badge variant="success">completed</Badge>;
    case "processing": return <Badge variant="warning">processing</Badge>;
    case "failed":     return <Badge variant="destructive">failed</Badge>;
    case "pending":    return <Badge variant="outline">pending</Badge>;
    default:           return <Badge variant="secondary">{status}</Badge>;
  }
}
