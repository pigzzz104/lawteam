/**
 * Compile-time contract verification between hand-written `api.ts` types and
 * FastAPI's generated `openapi.generated.ts` schemas. Zero runtime footprint.
 */

import type * as Api from "./api";
import type { components } from "./openapi.generated";

type Schema = components["schemas"];

type AssertEqual<A, B> =
  (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : never;

type AssertKeysExist<Local, Generated> =
  Exclude<keyof Local, keyof Generated> extends never ? true : never;

type RequiredKeys<T> = {
  [K in keyof T]-?: Record<string, never> extends Pick<T, K> ? never : K;
}[keyof T];

type AssertCoversRequired<Local, Generated> =
  Exclude<RequiredKeys<Generated>, keyof Local> extends never ? true : never;

type ExpectAll<T extends readonly true[]> = T;

export type _ContractChecks = ExpectAll<[
  // Enum exact equality
  AssertEqual<Api.SystemRole, Schema["SystemRole"]>,
  AssertEqual<Api.CaseVisibility, Schema["CaseVisibility"]>,
  AssertEqual<Api.CaseMemberRole, Schema["CaseMemberRole"]>,
  AssertEqual<Api.CaseCategory, Schema["CaseCategory"]>,
  AssertEqual<Api.HolidayRollMode, Schema["HolidayRollMode"]>,
  AssertEqual<Api.RiskLevel, Schema["RiskLevel"]>,
  AssertEqual<Api.NotifyTargetType, Schema["NotifyTargetType"]>,
  AssertEqual<Api.TriageStatus, Schema["TriageStatus"]>,

  // Field-level contract parity (local keys exist on backend & required backend keys are present locally)
  AssertKeysExist<Api.UserSelfRead, Schema["UserSelfRead"]>,
  AssertCoversRequired<Api.UserSelfRead, Schema["UserSelfRead"]>,

  AssertKeysExist<Api.UserSummary, Schema["UserSummary"]>,
  AssertCoversRequired<Api.UserSummary, Schema["UserSummary"]>,

  AssertKeysExist<Api.TokenResponse, Schema["TokenResponse"]>,
  AssertCoversRequired<Api.TokenResponse, Schema["TokenResponse"]>,

  AssertKeysExist<Api.CaseMemberRead, Schema["CaseMemberRead"]>,
  AssertCoversRequired<Api.CaseMemberRead, Schema["CaseMemberRead"]>,

  AssertKeysExist<Api.CaseRecord, Schema["Case"]>,
  AssertCoversRequired<Api.CaseRecord, Schema["Case"]>,

  AssertKeysExist<Api.CaseListItem, Schema["CaseListItem"]>,
  AssertCoversRequired<Api.CaseListItem, Schema["CaseListItem"]>,

  AssertKeysExist<Api.CaseEvent, Schema["CaseEvent"]>,
  AssertCoversRequired<Api.CaseEvent, Schema["CaseEvent"]>,

  AssertKeysExist<Api.PersonalAlertItem, Schema["PersonalAlertItem"]>,
  AssertCoversRequired<Api.PersonalAlertItem, Schema["PersonalAlertItem"]>,

  AssertKeysExist<Api.Deadline, Schema["Deadline"]>,
  AssertCoversRequired<Api.Deadline, Schema["Deadline"]>,

  AssertKeysExist<Api.ScheduleItem, Schema["ScheduleItem"]>,
  AssertCoversRequired<Api.ScheduleItem, Schema["ScheduleItem"]>,

  AssertKeysExist<Api.DocumentRecord, Schema["Document"]>,
  AssertCoversRequired<Api.DocumentRecord, Schema["Document"]>,

  AssertKeysExist<Api.CaseDetailRead, Schema["CaseDetailRead"]>,
  AssertCoversRequired<Api.CaseDetailRead, Schema["CaseDetailRead"]>,

  AssertKeysExist<Api.CaseEventResponse, Schema["CaseEventResponse"]>,
  AssertCoversRequired<Api.CaseEventResponse, Schema["CaseEventResponse"]>,

  AssertKeysExist<Api.AuditLogRead, Schema["AuditLogRead"]>,
  AssertCoversRequired<Api.AuditLogRead, Schema["AuditLogRead"]>,

  AssertKeysExist<Api.CalendarEventItem, Schema["CalendarEventItem"]>,
  AssertCoversRequired<Api.CalendarEventItem, Schema["CalendarEventItem"]>,

  AssertKeysExist<Api.CalendarConflict, Schema["CalendarConflict"]>,
  AssertCoversRequired<Api.CalendarConflict, Schema["CalendarConflict"]>,

  AssertKeysExist<Api.CalendarResponse, Schema["CalendarResponse"]>,
  AssertCoversRequired<Api.CalendarResponse, Schema["CalendarResponse"]>,

  AssertKeysExist<Api.DeadlineRule, Schema["DeadlineRule"]>,
  AssertCoversRequired<Api.DeadlineRule, Schema["DeadlineRule"]>,

  AssertKeysExist<Api.RuleRecalculateResponse, Schema["RuleRecalculateResponse"]>,
  AssertCoversRequired<Api.RuleRecalculateResponse, Schema["RuleRecalculateResponse"]>,

  AssertKeysExist<Api.AutoTriagePolicy, Schema["AutoTriagePolicy"]>,
  AssertCoversRequired<Api.AutoTriagePolicy, Schema["AutoTriagePolicy"]>,

  AssertKeysExist<Api.InboxTriageRead, Schema["InboxTriageRead"]>,
  AssertCoversRequired<Api.InboxTriageRead, Schema["InboxTriageRead"]>,

  AssertKeysExist<Api.InboxConfirmRequest, Schema["InboxConfirmRequest"]>,
  AssertCoversRequired<Api.InboxConfirmRequest, Schema["InboxConfirmRequest"]>,

  AssertKeysExist<Api.InboxConfirmResponse, Schema["InboxConfirmResponse"]>,
  AssertCoversRequired<Api.InboxConfirmResponse, Schema["InboxConfirmResponse"]>,

  AssertKeysExist<Api.ReminderDelivery, Schema["ReminderDeliveryRead"]>,
  AssertCoversRequired<Api.ReminderDelivery, Schema["ReminderDeliveryRead"]>,

  AssertKeysExist<Api.ReminderSweepResponse, Schema["ReminderSweepResponse"]>,
  AssertCoversRequired<Api.ReminderSweepResponse, Schema["ReminderSweepResponse"]>,

  AssertKeysExist<Api.TestPushResponse, Schema["TestPushResponse"]>,
  AssertCoversRequired<Api.TestPushResponse, Schema["TestPushResponse"]>,
]>;
