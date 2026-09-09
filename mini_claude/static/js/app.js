/* 应用入口：创建 Vue 应用并挂载。 */
(function () {
  "use strict";

  const { createApp } = Vue;
  const { TopBar, SideBar, ChatMain } = Components;

  /* 多根模板：三个根组件直接成为 #app 的 grid 子项，
   * 布局 CSS（topbar 跨两列 / sidebar 左 / main 右）无需任何调整。 */
  const App = {
    components: { TopBar, SideBar, ChatMain },
    setup() {
      Store.init();
      return { Store };
    },
    template: `
      <TopBar :busy="Store.store.busy" @new-chat="Store.newChat()"></TopBar>
      <SideBar></SideBar>
      <ChatMain></ChatMain>`,
  };

  createApp(App).mount("#app");
})();
