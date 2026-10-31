const {test} = require('node:test');
const assert = require('node:assert/strict');
const {createChat} = require('../apps/suke_ops/suke_ops/public/bear-control.js');
global.crypto = require('node:crypto').webcrypto;
global.sessionStorage = {getItem: () => '59a502bc-4ab4-4bad-aa1d-f1e014646bed', setItem() {}};
function setup(responses) {
    const calls = [];
    const chat = createChat({session: {user: 'test-user'}, call: async request => {
        calls.push(request);
        const message = await responses[request.method.split('.').pop()](request.args);
        return {message};
    }});
    return {chat, calls};
}
const request = message => ({message, signal: new AbortController().signal});
test('消息进入总控并沿用主页会话，返回真实任务答复', async () => {
    const {chat, calls} = setup({chat_status: () => ({model_ready: true}), start: () => ({name: 'TURN-1'}), poll: () => ({state: 'done', answer: '<script>不会执行</script>总控答复'})});
    assert.match(await chat(request('检查库存')), /总控答复/);
    assert.equal(calls[1].args.module, 'control');
    assert.equal(calls[1].args.question, '检查库存');
    assert.equal(calls[1].args.conversation, sessionStorage.getItem());
    assert.ok(calls.every(x => x.method.startsWith('suke_ops.hub.')));
});
test('模型未配置时不提交任务', async () => {
    const {chat, calls} = setup({chat_status: () => ({model_ready: false})});
    await assert.rejects(chat(request('你好')), /尚未配置/);
    assert.equal(calls.length, 1);
});
test('总控请求携带服务端声明版本，兼容采购申请门槛', async () => {
    const {chat,calls}=setup({chat_status:()=>({model_ready:true,client_version:'procurement-approval-v1'}),start:()=>({name:'T'}),poll:()=>({state:'done',answer:'已核实'})});
    await chat(request('查订单'));
    assert.equal(calls.find(x=>x.method.endsWith('.start')).args.client_version,'procurement-approval-v1');
});
test('失败任务和空回复不伪装成成功', async () => {
    for (const result of [{state: 'failed'}, {state: 'done', answer: ''}]) {
        const {chat} = setup({chat_status: () => ({model_ready: true}), start: () => ({name: 'T'}), poll: () => result});
        await assert.rejects(chat(request('你好')), /未完成|有效答复/);
    }
});
test('停止后保留已提交任务编号，未结束前禁止创建第二项任务', async () => {
    const controller = new AbortController();
    const {chat, calls} = setup({chat_status: () => ({model_ready: true}), start: () => {controller.abort(); return {name: 'T'};}, poll: () => ({state: 'running'})});
    await assert.rejects(chat({message: '第一条', signal: controller.signal}), {name: 'AbortError'});
    await assert.rejects(chat(request('第二条')), /后台运行/);
    assert.equal(calls.filter(x => x.method.endsWith('.start')).length, 1);
});
test('等待中的轮询可中止，后续不会继续请求', async () => {
    const controller = new AbortController();
    const {chat, calls} = setup({chat_status: () => ({model_ready: true}), start: () => ({name: 'T'}), poll: () => {setTimeout(() => controller.abort(), 5); return {state: 'running'};}});
    await assert.rejects(chat({message: '检查', signal: controller.signal}), {name: 'AbortError'});
    assert.equal(calls.filter(x => x.method.endsWith('.poll')).length, 1);
});
test('空输入、过长输入和已停止请求不调用服务端', async () => {
    const {chat, calls} = setup({});
    await assert.rejects(chat(request(' ')));
    await assert.rejects(chat(request('字'.repeat(2001))));
    const controller = new AbortController(); controller.abort();
    await assert.rejects(chat({message: '你好', signal: controller.signal}));
    assert.equal(calls.length, 0);
});
test('卡住任务过期后允许服务端 start 清理并重试', async () => {
    const controller = new AbortController();
    let count = 0;
    const originalNow = Date.now;
    let now = originalNow();
    Date.now = () => now;
    try {
        const {chat} = setup({chat_status: () => ({model_ready: true}), start: () => {
            if (++count === 1) controller.abort();
            return {name: 'T' + count};
        }, poll: () => count === 1 ? {state: 'queued'} : {state: 'done', answer: '已恢复'}});
        await assert.rejects(chat({message: '第一条', signal: controller.signal}));
        now += 960001;
        assert.match(await chat(request('重试')), /已恢复/);
        assert.equal(count, 2);
    } finally { Date.now = originalNow; }
});
