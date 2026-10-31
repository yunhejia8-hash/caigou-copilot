(function(root){
function calc(q){if(!Number.isInteger(q)||q<0||q>600)throw Error('请输入0至600的整数');const free=460-180-60, sales=free+q>=300?300:0,remaining=free+q-sales;return {free,sales,originalShortage:540-sales,nextProduction:remaining+200>=240?240:0,cost:q===0?0:q*1.2+300,remaining};}
const engine={calculate:calc};
if(typeof module==='object'&&module.exports) module.exports=engine;
else root.SupplyScenarioEngine=engine;
})(typeof window==='undefined'?globalThis:window);
