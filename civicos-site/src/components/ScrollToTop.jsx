import { useEffect } from "react";
import { useLocation } from "react-router-dom";

const TITLES = {
  "/": "CivicOS",
  "/building-blocks": "Building blocks roadmap — CivicOS",
  "/about": "About CivicOS",
  "/contact": "Contact — CivicOS",
  "/docs": "Docs — CivicOS",
  "/docs/consent": "Consent Docs — CivicOS",
};

export default function ScrollToTop() {
  const { pathname } = useLocation();

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "auto" });
    document.title = TITLES[pathname] ?? "CivicOS";
  }, [pathname]);

  return null;
}
