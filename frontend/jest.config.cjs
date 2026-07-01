module.exports = {
  testEnvironment: "jsdom",
  transform: {
    "^.+\\.tsx?$": "<rootDir>/__mocks__/viteEnvTransform.cjs",
  },
  moduleNameMapper: {
    "\\.(css|less|scss|sass)$": "identity-obj-proxy",
    "\\.(jpg|jpeg|png|gif|svg|ico)$": "<rootDir>/__mocks__/fileMock.cjs",
  },
  setupFiles: ["<rootDir>/__mocks__/importMeta.cjs"],
  setupFilesAfterEnv: ["@testing-library/jest-dom"],
};
