import { AlertTriangle, CheckCircle2, Clock, ShieldAlert } from "lucide-react";
import {
  CASE_CATEGORY_META,
  RISK_LEVEL_META,
  TRIAGE_STATUS_META,
} from "@/lib/domain";
import type { CaseCategory, RiskLevel, TriageStatus } from "@/types/api";

export function CategoryBadge({ category }: { category: CaseCategory }) {
  const meta = CASE_CATEGORY_META[category];
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${meta.badgeClass}`}
    >
      {meta.label}
    </span>
  );
}

export function RiskBadge({ risk }: { risk: RiskLevel }) {
  const meta = RISK_LEVEL_META[risk];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-semibold ${meta.badgeClass}`}
    >
      {risk === "critical" && <ShieldAlert className="h-3 w-3" aria-hidden="true" />}
      {risk === "high" && <AlertTriangle className="h-3 w-3" aria-hidden="true" />}
      {meta.label}
    </span>
  );
}

export function TriageStatusBadge({ status }: { status: TriageStatus }) {
  const meta = TRIAGE_STATUS_META[status] ?? {
    label: status,
    badgeClass: "bg-[#eceff5] text-[#434955] border-[#cdd5e2]",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium ${meta.badgeClass}`}
    >
      {status === "confirmed" && <CheckCircle2 className="h-3 w-3" aria-hidden="true" />}
      {status === "pending" && <Clock className="h-3 w-3" aria-hidden="true" />}
      {status === "manual" && (
        <AlertTriangle className="h-3 w-3" aria-hidden="true" />
      )}
      {meta.label}
    </span>
  );
}

export function CustodyBadge({
  status,
  place,
}: {
  status: string | null | undefined;
  place?: string | null;
}) {
  if (!status) return null;
  const inCustody = status.includes("在押") || status.includes("刑拘") || status.includes("逮捕");
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium ${
        inCustody
          ? "border-[#f4b8b3] bg-[#fde7e5] text-[#8c1d18]"
          : "border-[#b3e6d3] bg-[#dcf5ec] text-[#0b5742]"
      }`}
    >
      <span>羁押状态：{status}</span>
      {place ? <span className="opacity-85">（{place}）</span> : null}
    </span>
  );
}
