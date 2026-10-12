import type { ReactNode } from "react";
import SectionHeader from "../components/SectionHeader";
import CapabilityBadge from "../components/CapabilityBadge";
import type { CapabilityKey } from "../capabilities";

// Capability-context header for each Operate tab: a CapabilityBadge naming the
// AgentCore capability + a title + a one-line description of what the viewer is
// looking at. Built on the shared SectionHeader primitive.

export default function TabHeader({
  capability,
  suffix,
  title,
  description,
  actions,
}: {
  capability: CapabilityKey;
  suffix: string;
  title: string;
  description: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <SectionHeader
      title={title}
      subtitle={description}
      badge={<CapabilityBadge capability={capability} suffix={suffix} />}
      actions={actions}
    />
  );
}
