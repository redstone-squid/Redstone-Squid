import type { PropsWithChildren } from "react";
import { noticeStyles, type NoticeProps } from "../variants";

export function Notice({ title, tone, role, children }: PropsWithChildren<NoticeProps>) {
  return (
    <div className={noticeStyles({ tone })} role={role}>
      {title && <strong>{title}</strong>}
      {children}
    </div>
  );
}
