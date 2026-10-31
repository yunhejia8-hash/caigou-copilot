/* 小熊复用登录账号的总控会话；模型调用和业务权限仍由服务端处理。 */
(() => {
    function createChat(frappe) {
        let pending = null;
        let pendingAt = 0;
        let sending = false;
        let fallbackConversation = crypto.randomUUID();
        const userError = message => Object.assign(new Error(message), {userMessage: message});
        const key = 'supply-hub:' + frappe.session.user + ':control';
        const call = async (method, args = {}) => {
            const response = await frappe.call({method: 'suke_ops.hub.' + method, args});
            if (response.exc || response.message == null) throw userError('总控请求失败，请重试。');
            return response.message;
        };
        const aborted = signal => {
            if (signal.aborted) throw new DOMException('已停止等待，后台分析可能仍在运行。', 'AbortError');
        };
        const wait = signal => new Promise((resolve, reject) => {
            const stop = () => { clearTimeout(timer); reject(new DOMException('停止等待', 'AbortError')); };
            const timer = setTimeout(() => { signal.removeEventListener('abort', stop); resolve(); }, 1200);
            signal.addEventListener('abort', stop, {once: true});
            if (signal.aborted) stop();
        });
        return async ({message, signal}) => {
            aborted(signal);
            if (sending) throw userError('上一条分析尚未完成，请稍候。');
            if (!message.trim() || message.length > 2000) throw userError('请输入 1–2000 字的问题。');
            sending = true;
            try {
                if (pending) {
                    const previous = await call('poll', {name: pending});
                    aborted(signal);
                    // start 会清理账号内超过 16 分钟的卡住任务，不能在前端永久拦住它。
                    if (!['done', 'failed'].includes(previous.state) && Date.now() - pendingAt < 960000) throw userError('上一条分析仍在后台运行，请稍后重试。');
                    pending = null;
                }
                const status = await call('chat_status');
                aborted(signal);
                if (!status.model_ready) throw userError('尚未配置模型服务，请联系管理员。');
                let conversation;
                try { conversation = sessionStorage.getItem(key); } catch (_) { /* 禁用存储时使用本次会话 */ }
                conversation = conversation || fallbackConversation;
                fallbackConversation = conversation;
                try { sessionStorage.setItem(key, conversation); } catch (_) { /* 不在浏览器存储业务内容 */ }
                // 不取消已提交的 start 请求，确保取得任务编号，避免停止后盲目重复创建任务。
                const turn = await call('start', {question: message, module: 'control', conversation, request_id: crypto.randomUUID()});
                pending = turn.name;
                pendingAt = Date.now();
                aborted(signal);
                const deadline = Date.now() + 950000;
                while (Date.now() < deadline) {
                    const result = await call('poll', {name: pending});
                    aborted(signal);
                    if (['done', 'failed'].includes(result.state)) {
                        pending = null;
                        if (result.state === 'failed') throw userError('总控分析未完成，请到总控主页查看记录后重试。');
                        if (typeof result.answer !== 'string' || !result.answer.trim()) throw userError('总控未返回有效答复，请到总控主页查看记录。');
                        return result.answer + '\n\n详细协作记录、草稿与审批入口请到总控主页查看。';
                    }
                    await wait(signal);
                }
                throw userError('分析仍在后台运行，请到总控主页查看记录。');
            } finally { sending = false; }
        };
    }
    // 保留纯函数入口供契约测试使用，不依赖浏览器或真实模型。
    if (typeof module !== 'undefined' && module.exports) { module.exports = {createChat}; return; }
    $(document).ready(() => {
        if (!frappe.session?.user || frappe.session.user === 'Guest' || window.supplyBear) return;
        const chat = createChat(frappe);
        window.supplyBear = BearPet.mount({
            name: '发财熊', size: 140, roam: false, timeoutMs: 960000,
            storageKey: 'supply-bear:' + frappe.session.user,
            onMessage: chat
        });
        window.addEventListener('pagehide', event => {
            if (!event.persisted) { window.supplyBear?.destroy(); window.supplyBear = null; }
        });
    });
})();
