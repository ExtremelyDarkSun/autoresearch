// GENERATED-BY: portable synchronization (DSH agent, 2026-11-13 +08:00)
// LAST-EDIT: portable synchronization (DSH agent, 2026-11-13 +08:00)
// PURPOSE: 纯客户端显示插件 —— 把已有 goal-observer / goal-observer-error 记录拼到「继续执行目标」展开体的开头；
//          只生成新的显示副本，不写回会话、不调用模型、不改 goal 与消息原件。
// ORIGIN: install/dsh-goal-observer-ui/client.js（2026-11-13 读码提取）
// CUMULATIVE-DELTA: 首版。相对原实现：① 缺原生渲染器时不再 throw 把页面打崩，降级为 null + 只 warn 一次；
//          ② 快照/条目/内容全做形状容错（undefined、非数组、空 data 都不会抛）；③ 匹配键统一成
//          goalId/revision/round 三元组（缺字段归一为空串/0），仍不改原 node、原 source、原 content 块引用。
window.__ModuleLoader__.load({
  id: '@autoresearch/dsh-goal-observer-ui',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    const EMPTY = Object.freeze({ entries: Object.freeze([]) });
    const OBSERVER_KINDS = new Set(['goal-observer', 'goal-observer-error']);
    const ACTION_LABELS = Object.freeze({ continue: '继续', pause: '暂停', complete: '完成' });
    const warned = { missingOriginal: false };

    function keyOf(source) {
      return JSON.stringify([
        typeof source?.goalId === 'string' ? source.goalId : '',
        Number.isSafeInteger(source?.revision) ? source.revision : 0,
        Number.isSafeInteger(source?.round) ? source.round : 0,
      ]);
    }

    function indexObservers(entries) {
      const result = new Map();
      if (!Array.isArray(entries)) return result;
      for (const entry of entries) {
        const event = entry?.event ?? entry;
        if (event?.type !== 'user/message') continue;
        const message = event.data;
        const source = message?.source;
        if (!source || !OBSERVER_KINDS.has(source.kind)) continue;
        result.set(keyOf(source), {
          source,
          content: Array.isArray(message.content) ? message.content : [],
          seq: event.seq,
        });
      }
      return result;
    }

    function observerText(record) {
      const source = record?.source ?? {};
      const lines = ['观察者本轮指令', `轮次：${source.round ?? '?'} · 来源：${source.kind ?? 'goal-observer'}`];
      const decision = source.decision;
      if (decision && typeof decision === 'object') {
        const action = String(decision.action ?? '');
        lines.push(`决定：${ACTION_LABELS[action] ?? action}`);
        if (decision.instruction) lines.push('', '下一步：', String(decision.instruction));
        if (decision.evidence) lines.push('', '判断依据：', String(decision.evidence));
      } else {
        const text = (Array.isArray(record?.content) ? record.content : [])
          .filter(block => block?.type === 'text' && typeof block.text === 'string')
          .map(block => block.text)
          .join('\n');
        lines.push('', text || '（观察者没有留下文字说明）');
      }
      lines.push('', '此内容由观察者生成，不代表新的用户授权。', '', '──────────', '原生目标续跑提示', '');
      return lines.join('\n');
    }

    /** 只给 goal 通知做「显示副本」：原 node / source / content 块引用一律不动。 */
    function presentationNode(node, observers) {
      if (node?.data?.source?.kind !== 'goal') return node;
      const record = observers.get(keyOf(node.data.source));
      if (!record) return node;
      const originalContent = Array.isArray(node.data.content) ? node.data.content : [];
      return {
        ...node,
        data: {
          ...node.data,
          content: [{ type: 'text', text: observerText(record) }, ...originalContent],
        },
      };
    }

    function findTurnTrigger(ctx) {
      try {
        const entries = typeof ctx.slots.entries === 'function' ? ctx.slots.entries('conversation.chat.node') : [];
        return (Array.isArray(entries) ? entries : []).find(
          entry => entry?.options?.key === 'turn-trigger' && (entry.options.priority ?? 0) === 0,
        );
      } catch {
        return undefined;
      }
    }

    function warnMissingOriginal() {
      if (warned.missingOriginal) return;
      warned.missingOriginal = true;
      try {
        globalThis.console?.warn?.('goal-observer-ui: 缺少原生目标通知渲染器（conversation.chat.node/turn-trigger），本次不显示观察者段落');
      } catch {
        /* 没 console 也不影响渲染 */
      }
    }

    return {
      inject: ['slots', 'sessions'],
      apply(ctx) {
        ctx.slots.inject('conversation.chat.node', () => {
          function ObserverTrigger(props) {
            // 插槽声明时各插件的 inject 回调尚未全部执行；渲染时才查原组件。
            const original = findTurnTrigger(ctx);
            const useEvents = props?.useGoalObserverEvents;
            const snapshot = typeof useEvents === 'function' ? useEvents(value => value) : EMPTY;
            const records = React.useMemo(() => indexObservers(snapshot?.entries), [snapshot]);
            const node = React.useMemo(() => presentationNode(props?.node, records), [props?.node, records]);
            if (!original) {
              warnMissingOriginal();
              return null;
            }
            return h(original.component, { ...props, node });
          }
          return ctx.slots.register({
            name: 'conversation.chat.node',
            key: 'turn-trigger',
            priority: -100,
            locale: 'chat',
            inject: sessionId => {
              let eventSource;
              try {
                eventSource = ctx.sessions?.binding?.(sessionId)?.eventSource;
              } catch {
                eventSource = undefined;
              }
              return {
                hooks: {
                  goalObserverEvents: {
                    getSnapshot: () => {
                      try {
                        return typeof eventSource?.getSnapshot === 'function' ? eventSource.getSnapshot() : EMPTY;
                      } catch {
                        return EMPTY;
                      }
                    },
                    subscribe: listener => {
                      try {
                        return typeof eventSource?.subscribe === 'function' ? eventSource.subscribe(listener) : () => {};
                      } catch {
                        return () => {};
                      }
                    },
                  },
                },
              };
            },
          }, ObserverTrigger);
        });
      },
    };
  },
});
