// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import axios from "axios";

const BASE_URL = import.meta.env.VITE_API_URL || "";

export const apiClient = axios.create({
  baseURL: BASE_URL,
  headers: { "Content-Type": "application/json" },
});

export function setAuthToken(token: string | null) {
  if (token) {
    apiClient.defaults.headers.common["Authorization"] = `Bearer ${token}`;
    localStorage.setItem("nexplane_token", token);
  } else {
    delete apiClient.defaults.headers.common["Authorization"];
    localStorage.removeItem("nexplane_token");
  }
}

export function loadStoredToken() {
  const token = localStorage.getItem("nexplane_token");
  if (token) {
    apiClient.defaults.headers.common["Authorization"] = `Bearer ${token}`;
  }
  return token;
}

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      setAuthToken(null);
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);
