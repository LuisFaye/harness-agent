import { PageHeader } from "@/components/layout/page-header";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Blocks, Database, RefreshCw, ServerCog, Wrench } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/features/management/confirm-dialog";
import {
  adoptSkill,
  deleteMcpServer,
  deleteDatabaseConnection,
  deleteSkill,
  getSkillDetail,
  importSkill,
  listMcpServers,
  listDatabaseConnections,
  listSkills,
  listTools,
  runMcpAction,
  saveMcpServer,
  saveDatabaseConnection,
  testDatabaseConnection,
  updateSkillStatus,
  updateToolStatus,
  type McpServerWritePayload,
  type DatabaseConnectionWritePayload,
} from "@/lib/api/management";
import type { DatabaseConnection, McpServer, SkillSummary, ToolDefinition } from "@/lib/api/management-schemas";

import { DatabaseConnectionsPanel } from "./database-connections-panel";
import { McpPanel } from "./mcp-panel";
import { SkillDetailDialog } from "./skill-detail-dialog";
import { SkillsPanel } from "./skills-panel";
import { ToolsPanel } from "./tools-panel";

type CapabilityTab = "tools" | "skills" | "database" | "mcp";

export function CapabilitiesPage() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<CapabilityTab>("tools");
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedSkill, setSelectedSkill] = useState<SkillSummary | null>(null);
  const [deleteSkillTarget, setDeleteSkillTarget] = useState<SkillSummary | null>(null);
  const toolsQuery = useQuery({ queryKey: ["tools"], queryFn: listTools });
  const skillsQuery = useQuery({ queryKey: ["skills"], queryFn: listSkills });
  const mcpQuery = useQuery({ queryKey: ["mcp-servers"], queryFn: listMcpServers });
  const databaseQuery = useQuery({ queryKey: ["database-connections"], queryFn: listDatabaseConnections });
  const skillDetailQuery = useQuery({
    queryKey: ["skill-detail", selectedSkill?.name],
    queryFn: () => getSkillDetail(selectedSkill!.name),
    enabled: Boolean(selectedSkill),
  });

  const toolMutation = useMutation({
    mutationFn: (tool: ToolDefinition) => updateToolStatus(tool.tool_id, !tool.enabled),
    onSuccess: async (item) => {
      setNotice(`${item.name} 已${item.enabled ? "启用" : "停用"}。`);
      await queryClient.invalidateQueries({ queryKey: ["tools"] });
    },
  });
  const skillMutation = useMutation({
    mutationFn: ({ action, skill }: {
      action: "enable" | "disable" | "adopt";
      skill: SkillSummary;
    }) => action === "adopt"
      ? adoptSkill(skill.name)
      : updateSkillStatus(skill.name, action === "enable"),
    onSuccess: async (payload, variables) => {
      setNotice(`${payload.item.name} 已更新。`);
      setSelectedSkill((current) => current?.name === variables.skill.name
        ? {
            ...current,
            status: variables.action === "disable" ? "disabled" : "enabled",
            enabled: variables.action !== "disable",
          }
        : current);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["skills"] }),
        queryClient.invalidateQueries({ queryKey: ["tools"] }),
        queryClient.invalidateQueries({ queryKey: ["skill-detail", variables.skill.name] }),
      ]);
    },
  });
  const skillImportMutation = useMutation({
    mutationFn: importSkill,
    onSuccess: async (payload) => {
      setNotice(`已导入 ${payload.items.length} 个 Skill。`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["skills"] }),
        queryClient.invalidateQueries({ queryKey: ["tools"] }),
      ]);
    },
  });
  const skillDeleteMutation = useMutation({
    mutationFn: (skill: SkillSummary) => deleteSkill(skill.name),
    onSuccess: async (payload) => {
      setNotice(`${payload.name} 已删除。`);
      setSelectedSkill(null);
      setDeleteSkillTarget(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["skills"] }),
        queryClient.invalidateQueries({ queryKey: ["tools"] }),
      ]);
    },
  });
  const mcpMutation = useMutation({
    mutationFn: async (variables:
      | { kind: "save"; payload: McpServerWritePayload; serverId?: string }
      | { kind: "import"; payloads: McpServerWritePayload[]; connectAfterImport: boolean }
      | { kind: "action"; server: McpServer; action: "connect" | "disconnect" | "sync" | "toggle" }
      | { kind: "delete"; server: McpServer },
    ) => {
      if (variables.kind === "save") {
        return saveMcpServer(variables.payload, variables.serverId);
      }
      if (variables.kind === "import") {
        const imported = [];
        for (const payload of variables.payloads) {
          const saved = await saveMcpServer(payload);
          if (variables.connectAfterImport && saved.enabled) {
            try {
              imported.push(await runMcpAction(saved.server_id, "connect"));
            } catch {
              imported.push(saved);
            }
          } else {
            imported.push(saved);
          }
        }
        return { imported };
      }
      if (variables.kind === "delete") {
        await deleteMcpServer(variables.server.server_id);
        return variables.server;
      }
      if (variables.action === "toggle") {
        return saveMcpServer(toMcpPayload(variables.server, !variables.server.enabled), variables.server.server_id);
      }
      return runMcpAction(variables.server.server_id, variables.action);
    },
    onSuccess: async (result) => {
      setNotice("imported" in result
        ? `已导入 ${result.imported.length} 个 MCP，连接结果已刷新。`
        : `${result.name} 已更新。`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["mcp-servers"] }),
        queryClient.invalidateQueries({ queryKey: ["tools"] }),
      ]);
    },
  });
  const databaseMutation = useMutation({
    mutationFn: async (variables:
      | { kind: "save"; payload: DatabaseConnectionWritePayload; connectionId?: string }
      | { kind: "test"; connection: DatabaseConnection }
      | { kind: "delete"; connection: DatabaseConnection },
    ) => {
      if (variables.kind === "save") {
        return saveDatabaseConnection(variables.payload, variables.connectionId);
      }
      if (variables.kind === "test") {
        return testDatabaseConnection(variables.connection.connection_id);
      }
      await deleteDatabaseConnection(variables.connection.connection_id);
      return variables.connection;
    },
    onSuccess: async (_, variables) => {
      setNotice(
        variables.kind === "delete"
          ? `${variables.connection.name} 已删除。`
          : variables.kind === "test"
            ? `${variables.connection.name} 连接正常。`
            : "数据库连接已保存并通过测试。",
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["database-connections"] }),
        queryClient.invalidateQueries({ queryKey: ["tools"] }),
        queryClient.invalidateQueries({ queryKey: ["mcp-servers"] }),
      ]);
    },
  });

  const mutationError = toolMutation.error
    || skillMutation.error
    || skillImportMutation.error
    || skillDeleteMutation.error
    || mcpMutation.error;
  const effectiveMutationError = mutationError || databaseMutation.error;
  const refreshing = toolsQuery.isFetching || skillsQuery.isFetching || mcpQuery.isFetching || databaseQuery.isFetching;
  const tools = toolsQuery.data?.items ?? [];
  const skills = skillsQuery.data?.items ?? [];
  const servers = mcpQuery.data ?? [];
  const databases = databaseQuery.data?.items ?? [];

  async function refreshAll() {
    setNotice(null);
    await Promise.all([toolsQuery.refetch(), skillsQuery.refetch(), databaseQuery.refetch(), mcpQuery.refetch()]);
  }

  function handleSkillAction(
    action: "enable" | "disable" | "adopt" | "delete",
    skill: SkillSummary,
  ) {
    if (action === "delete") {
      setDeleteSkillTarget(skill);
      return;
    }
    skillMutation.mutate({ action, skill });
  }

  return (
    <section aria-labelledby="capabilities-title" className="mx-auto w-full max-w-6xl">
      <PageHeader title="能力中心" description="管理工具、技能与连接，查看 Agent 当前可使用的能力。" icon={Blocks} id="capabilities-title">
        <Button disabled={refreshing} onClick={refreshAll} variant="outline">
          <RefreshCw aria-hidden="true" className={refreshing ? "animate-spin" : ""} />
          刷新能力
        </Button>
      </PageHeader>

      <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Summary icon={<Wrench />} label="Agent Tools" value={`${tools.filter((item) => item.available).length}/${tools.length}`} />
        <Summary icon={<Blocks />} label="可调用 Skills" value={`${skills.filter((item) => item.callable_by_agent).length}/${skills.length}`} />
        <Summary icon={<Database />} label="数据库连接" value={`${databases.filter((item) => item.status === "connected").length}/${databases.length}`} />
        <Summary icon={<ServerCog />} label="已连接 MCP" value={`${servers.filter((item) => item.status === "connected").length}/${servers.length}`} />
      </div>

      {notice ? <div className="mt-5 rounded-xl border border-success/25 bg-success/5 px-4 py-3 text-sm text-success" role="status">{notice}</div> : null}
      {effectiveMutationError ? <div className="mt-5 rounded-xl border border-destructive/25 bg-destructive/5 px-4 py-3 text-sm text-destructive" role="alert">{effectiveMutationError.message}</div> : null}

      <div
        aria-label="能力类型"
        className="mt-6 inline-flex max-w-full items-center gap-1 overflow-x-auto rounded-2xl border border-border/85 bg-muted/65 p-1 shadow-2xs"
        role="tablist"
      >
        {([
          ["tools", "Tool"],
          ["skills", "Skill"],
          ["database", "数据库连接"],
          ["mcp", "MCP"],
        ] as const).map(([value, label]) => (
          <button
            aria-controls={`capability-panel-${value}`}
            aria-selected={tab === value}
            className={`min-h-9.5 shrink-0 rounded-xl px-4 text-sm font-medium whitespace-nowrap transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
              tab === value
                ? "bg-card font-semibold text-primary shadow-xs"
                : "text-muted-foreground hover:bg-card/60 hover:text-foreground"
            }`}
            id={`capability-tab-${value}`}
            key={value}
            onClick={() => {
              setTab(value);
              setNotice(null);
            }}
            role="tab"
            type="button"
          >
            {label}
          </button>
        ))}
      </div>

      <div
        aria-labelledby={`capability-tab-${tab}`}
        className="pt-5"
        id={`capability-panel-${tab}`}
        role="tabpanel"
      >
        {tab === "tools" ? (
          <QueryPanel error={toolsQuery.error?.message} loading={toolsQuery.isLoading}>
            <ToolsPanel
              items={tools}
              onToggle={(tool) => {
                setNotice(null);
                toolMutation.mutate(tool);
              }}
              pendingToolId={toolMutation.isPending ? toolMutation.variables.tool_id : null}
            />
          </QueryPanel>
        ) : null}
        {tab === "skills" ? (
          <QueryPanel error={skillsQuery.error?.message} loading={skillsQuery.isLoading}>
            <SkillsPanel
              importing={skillImportMutation.isPending}
              items={skills}
              onImport={(path) => {
                setNotice(null);
                skillImportMutation.mutate(path);
              }}
              onSelect={setSelectedSkill}
            />
          </QueryPanel>
        ) : null}
        {tab === "database" ? (
          <QueryPanel error={databaseQuery.error?.message} loading={databaseQuery.isLoading}>
            <DatabaseConnectionsPanel
              busy={databaseMutation.isPending}
              items={databases}
              onDelete={(connection) => databaseMutation.mutate({ kind: "delete", connection })}
              onSave={async (payload, connectionId) => {
                await databaseMutation.mutateAsync(
                  connectionId
                    ? { kind: "save", payload, connectionId }
                    : { kind: "save", payload },
                );
              }}
              onTest={(connection) => databaseMutation.mutate({ kind: "test", connection })}
            />
          </QueryPanel>
        ) : null}
        {tab === "mcp" ? (
          <QueryPanel error={mcpQuery.error?.message} loading={mcpQuery.isLoading}>
            <McpPanel
              busyKey={mcpMutation.isPending ? mcpBusyKey(mcpMutation.variables) : null}
              items={servers}
              onAction={(server, action) => {
                setNotice(null);
                mcpMutation.mutate({ kind: "action", server, action });
              }}
              onDelete={(server) => mcpMutation.mutate({ kind: "delete", server })}
              onImport={(payloads, connectAfterImport) => mcpMutation.mutate({
                kind: "import",
                payloads,
                connectAfterImport,
              })}
              onSave={(payload, serverId) => mcpMutation.mutate(
                serverId ? { kind: "save", payload, serverId } : { kind: "save", payload },
              )}
            />
          </QueryPanel>
        ) : null}
      </div>

      <SkillDetailDialog
        detail={skillDetailQuery.data}
        error={skillDetailQuery.error?.message ?? null}
        loading={skillDetailQuery.isLoading}
        onAction={handleSkillAction}
        onOpenChange={(open) => !open && setSelectedSkill(null)}
        pending={skillMutation.isPending}
        skill={selectedSkill}
      />
      <ConfirmDialog
        confirmLabel="删除 Skill"
        description={`删除后会移除 ${deleteSkillTarget?.name || "这个 Skill"} 的导入副本和注册状态，且无法从页面恢复。`}
        onConfirm={() => deleteSkillTarget && skillDeleteMutation.mutate(deleteSkillTarget)}
        onOpenChange={(open) => !open && setDeleteSkillTarget(null)}
        open={Boolean(deleteSkillTarget)}
        pending={skillDeleteMutation.isPending}
        title="确认删除 Skill？"
      />
    </section>
  );
}

function Summary({ icon, label, value }: { icon: React.ReactElement; label: string; value: string }) {
  return (
    <article className="flex items-center gap-3.5 rounded-2xl border border-border/85 bg-card p-4 shadow-2xs transition-all duration-150 hover:border-primary/25 hover:shadow-xs">
      <span
        aria-hidden="true"
        className="grid size-11 shrink-0 place-items-center rounded-xl border border-primary/15 bg-primary-soft text-primary shadow-2xs [&>svg]:size-5"
      >
        {icon}
      </span>
      <div className="min-w-0">
        <strong className="block text-2xl leading-7 font-semibold tracking-tight text-foreground tabular-nums">{value}</strong>
        <span className="mt-0.5 block truncate text-xs font-medium text-muted-foreground">{label}</span>
      </div>
    </article>
  );
}

function QueryPanel({ children, error, loading }: { children: React.ReactNode; error: string | undefined; loading: boolean }) {
  if (loading) {
    return <div className="grid min-h-64 place-items-center rounded-2xl border bg-card text-sm text-muted-foreground shadow-2xs" role="status">正在读取能力状态</div>;
  }
  if (error) {
    return <div className="rounded-2xl border border-destructive/25 bg-destructive/5 p-5 text-sm text-destructive" role="alert">{error}</div>;
  }
  return children;
}

function toMcpPayload(server: McpServer, enabled: boolean): McpServerWritePayload {
  return {
    name: server.name,
    transport_type: server.transport_type,
    command: server.command,
    args: server.args,
    url: server.url,
    environment_keys: server.environment_keys,
    header_env_map: server.header_env_map,
    enabled,
  };
}

function mcpBusyKey(variables: typeof undefined | (
  | { kind: "save"; payload: McpServerWritePayload; serverId?: string }
  | { kind: "import"; payloads: McpServerWritePayload[]; connectAfterImport: boolean }
  | { kind: "action"; server: McpServer; action: "connect" | "disconnect" | "sync" | "toggle" }
  | { kind: "delete"; server: McpServer }
)): string | null {
  if (!variables) {
    return null;
  }
  if (variables.kind === "save" || variables.kind === "import") {
    return variables.kind;
  }
  return `${variables.kind === "delete" ? "delete" : variables.action}:${variables.server.server_id}`;
}
