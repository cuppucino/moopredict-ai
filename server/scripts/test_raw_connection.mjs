import { createRequire } from 'module';
const require = createRequire(import.meta.url);

const futuApi = require('moomoo-api');
const ftapi = futuApi.default || futuApi;

console.log('Attempting raw connection to FutuOpenD at 127.0.0.1:11111...');

const client = new ftapi();

client.onlogin = (success, response) => {
  console.log('onlogin fired:', success, JSON.stringify(response));
  if (success) {
    console.log('✅ CONNECTED SUCCESSFULLY');
    process.exit(0);
  } else {
    console.log('❌ LOGIN FAILED:', response);
    process.exit(1);
  }
};

client.start('127.0.0.1', 11111, false);

console.log('start() called, waiting for onlogin callback...');

// Keep process alive for 30s
setTimeout(() => {
  console.log('❌ No response after 30 seconds. onlogin never fired.');
  process.exit(1);
}, 30000);
