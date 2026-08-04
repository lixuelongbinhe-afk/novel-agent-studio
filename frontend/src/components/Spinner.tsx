import { motion } from "framer-motion";

export function Spinner() {
  return (
    <div className="spinner-container" role="status" aria-label="正在打开">
      <motion.div
        className="spinner"
        animate={{ rotate: 360 }}
        transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
      />
    </div>
  );
}
