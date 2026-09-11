<script setup>
import { reactive, ref } from 'vue'
import { bootstrapAccount, loginAccount } from '../api/reports'

const props = defineProps({
  setupRequired: Boolean,
  workerDatabaseAuthEnabled: Boolean,
})
const emit = defineEmits(['authenticated'])
const loading = ref(false)
const error = ref('')
const form = reactive({ username: '', display_name: '', password: '' })

async function submit() {
  loading.value = true
  error.value = ''
  try {
    let result
    if (props.setupRequired) {
      result = await bootstrapAccount(form)
    } else {
      result = await loginAccount({
        username: form.username,
        password: form.password,
      })
    }
    emit('authenticated', result.user)
  } catch (exception) {
    error.value = exception.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-card">
      <div class="login-brand"><span></span><span></span><span></span></div>
      <p class="eyebrow">WORKSHOP PRODUCTION</p>
      <h1>{{ setupRequired ? '初始化管理员' : '系统登录' }}</h1>
      <p>{{ setupRequired ? '首次使用需要创建一个本地管理员账号，完成后可从 HR 人员库继续授权管理员。' : workerDatabaseAuthEnabled ? '使用公司工号或管理员账号登录，系统会自动识别账号权限并进入对应页面。' : '使用管理员账号和密码登录。' }}</p>
      <form @submit.prevent="submit">
        <label><span>{{ !setupRequired && workerDatabaseAuthEnabled ? '公司工号 / 管理员账号' : '管理员账号' }}</span><input v-model.trim="form.username" :minlength="setupRequired ? 2 : 1" maxlength="64" autocomplete="username" required /></label>
        <label v-if="setupRequired"><span>显示姓名</span><input v-model.trim="form.display_name" maxlength="64" required /></label>
        <label><span>密码</span><input v-model="form.password" type="password" :minlength="setupRequired ? 8 : 1" maxlength="128" autocomplete="current-password" required /></label>
        <div v-if="error" class="login-error">{{ error }}</div>
        <button class="primary" type="submit" :disabled="loading">{{ loading ? '正在提交' : setupRequired ? '创建并登录' : '登录' }}</button>
      </form>
    </section>
  </main>
</template>
