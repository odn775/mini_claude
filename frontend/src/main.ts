import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { createPinia } from 'pinia'
import { createApp } from 'vue'

import 'element-plus/dist/index.css'
// 放在 Element Plus 之后：用主题变量覆盖它的默认视觉
import './styles/main.css'

import App from './App.vue'

createApp(App).use(createPinia()).use(ElementPlus, { locale: zhCn }).mount('#app')
