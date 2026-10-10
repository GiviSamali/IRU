/* Local mock demo: one real site/renderer, no production API or devices. */
const {createServer}=require('../tests/helpers/smart-ui-fixtures.cjs');
const {server}=createServer({demo:true});
server.listen(Number(process.env.PORT||8770),'127.0.0.1',()=>console.log(`Smart UI mock demo: http://127.0.0.1:${server.address().port} — resize the same page to 320/480/1280px. All operations are synthetic.`));