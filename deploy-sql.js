#!/usr/bin/env node

/**
 * Supabase SQL 함수 배포 스크립트
 * supabase_auto_classify.sql을 Supabase에 자동으로 배포합니다.
 */

const fs = require('fs');
const path = require('path');

// supabase-config.js에서 설정 불러오기
const configPath = path.join(__dirname, 'supabase-config.js');
if (!fs.existsSync(configPath)) {
  console.error('❌ supabase-config.js 파일을 찾을 수 없습니다.');
  process.exit(1);
}

// window 객체를 전역에 생성하여 supabase-config.js 호환
global.window = {};
eval(fs.readFileSync(configPath, 'utf8'));

const SUPABASE_URL = global.window.SUPABASE_URL || process.env.SUPABASE_URL;
const SUPABASE_ANON_KEY = global.window.SUPABASE_ANON_KEY || process.env.SUPABASE_ANON_KEY;

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  console.error('❌ Supabase 설정을 찾을 수 없습니다.');
  process.exit(1);
}

// SQL 파일 읽기
const sqlPath = path.join(__dirname, 'supabase_auto_classify.sql');
if (!fs.existsSync(sqlPath)) {
  console.error('❌ supabase_auto_classify.sql 파일을 찾을 수 없습니다.');
  process.exit(1);
}

const sqlContent = fs.readFileSync(sqlPath, 'utf8');

console.log('📤 Supabase에 SQL 함수를 배포합니다...');
console.log(`📍 URL: ${SUPABASE_URL}`);
console.log('');

// Supabase REST API를 통해 SQL 실행
// 주의: ANON_KEY로는 SQL 실행이 불가능할 수 있습니다.
// Service Role Key가 필요할 수 있습니다.

fetch(`${SUPABASE_URL}/rest/v1/rpc/exec_sql`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'apikey': SUPABASE_ANON_KEY,
    'Authorization': `Bearer ${SUPABASE_ANON_KEY}`
  },
  body: JSON.stringify({ query: sqlContent })
})
.then(response => {
  if (!response.ok) {
    return response.text().then(text => {
      throw new Error(`HTTP ${response.status}: ${text}`);
    });
  }
  return response.json();
})
.then(data => {
  console.log('✅ SQL 함수가 성공적으로 배포되었습니다!');
  console.log('');
  console.log('📋 배포된 함수:');
  console.log('   - insert_transaction_from_device(p_line TEXT)');
  console.log('');
  console.log('🔍 주요 변경사항:');
  console.log('   ✓ 입금/출금 자동 구분 (p_line에 "입금" 키워드 체크)');
  console.log('   ✓ 입금 시: type=수입, main=급여, category=월급');
  console.log('   ✓ 출금 시: type=지출, main=생활비, category=식비');
  console.log('   ✓ 결제수단: 농협[관] (고정)');
  console.log('');
  console.log('💡 테스트 방법:');
  console.log('   MacroDroid에서 농협 알림을 다시 받아보세요.');
})
.catch(error => {
  console.error('❌ 배포 실패:', error.message);
  console.log('');
  console.log('🔧 수동 배포 방법:');
  console.log('   1. Supabase 대시보드 열기');
  console.log('   2. SQL Editor 메뉴 선택');
  console.log('   3. supabase_auto_classify.sql 파일 내용 복사');
  console.log('   4. 붙여넣기 후 Run 실행');
  console.log('');
  console.log('⚠️  참고: REST API로는 SQL 실행이 제한될 수 있습니다.');
  console.log('   Service Role Key가 필요하거나 수동 배포가 필요할 수 있습니다.');
  process.exit(1);
});
