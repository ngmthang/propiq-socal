import {Link} from "react-router-dom";

export default function DisclaimerBanner() {
    return (
      <div className="border-b border-line bg-sage/10 px-6
        py-2 text-xs text-ink/60"
      >
        Demo data - property listings and values shown are synthetic, not
        real transactions. Estimates are AI-generated model outputs, not
        appraisals; do not use them to make financial decisions.{" "}
        <Link to="/legal" className="text-terracotta hover:underline">
            Learn more
        </Link>
      </div>
    );
}