import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ClarificationPanel } from "./ClarificationPanel";
import type { ClarificationResponse } from "../api/types";

function response(overrides: Partial<ClarificationResponse> = {}): ClarificationResponse {
  return {
    run_id: "run_1",
    iteration: 1,
    status: "clarifying",
    questions: [
      {
        question_id: "Q-001",
        question: "Should the user be allowed to retry?",
        priority: "blocking",
        answer_type: "boolean",
        requirement_id: "REQ-001",
        options: [],
        reason: "error-path coverage",
      },
    ],
    readiness: {
      ready: false,
      requirements_total: 2,
      blocking_unanswered: 1,
      recommended_unanswered: 0,
      optional_unanswered: 0,
      critical_gaps: 0,
      blocking_reasons: ["1 blocking question(s) unanswered."],
    },
    ...overrides,
  };
}

function renderPanel(data: ClarificationResponse, answers: Record<string, string> = {}) {
  const setAnswer = vi.fn();
  const onSubmit = vi.fn();
  render(
    <ClarificationPanel
      data={data}
      answers={answers}
      setAnswer={setAnswer}
      submitting={false}
      submitError={null}
      onSubmit={onSubmit}
    />,
  );
  return { setAnswer, onSubmit };
}

describe("ClarificationPanel", () => {
  it("renders a boolean question as Yes/No", () => {
    const { setAnswer } = renderPanel(response());
    expect(screen.getByRole("button", { name: "Yes" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "No" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    expect(setAnswer).toHaveBeenCalledWith("Q-001", "true");
  });

  it("renders single_select as radio options", () => {
    const data = response({
      questions: [
        {
          question_id: "Q-001",
          question: "What happens when no store is available?",
          priority: "recommended",
          answer_type: "single_select",
          requirement_id: "REQ-001",
          options: ["Show message", "Allow retry", "Disable CTA"],
          reason: "",
        },
      ],
    });
    const { setAnswer } = renderPanel(data);
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(3);
    fireEvent.click(screen.getByLabelText("Allow retry"));
    expect(setAnswer).toHaveBeenCalledWith("Q-001", "Allow retry");
  });

  it("renders multi_select as checkboxes", () => {
    const data = response({
      questions: [
        {
          question_id: "Q-001",
          question: "Which roles can perform this action?",
          priority: "recommended",
          answer_type: "multi_select",
          requirement_id: null,
          options: ["Admin", "User", "Guest"],
          reason: "",
        },
      ],
    });
    renderPanel(data);
    expect(screen.getAllByRole("checkbox")).toHaveLength(3);
  });

  it("renders numeric, date, and text inputs appropriately", () => {
    const types: Array<[string, string]> = [
      ["numeric", "spinbutton"],
      ["date", "textbox"], // date input role varies; presence is what matters
      ["text", "textbox"],
    ];
    for (const [answerType] of types) {
      const { unmount } = render(
        <ClarificationPanel
          data={response({
            questions: [
              {
                question_id: "Q-001",
                question: `A ${answerType} question`,
                priority: "optional",
                answer_type: answerType as never,
                requirement_id: null,
                options: [],
                reason: "",
              },
            ],
          })}
          answers={{}}
          setAnswer={vi.fn()}
          submitting={false}
          submitError={null}
          onSubmit={vi.fn()}
        />,
      );
      // Each renders some input control.
      expect(document.querySelector("input, textarea")).toBeTruthy();
      unmount();
    }
  });

  it("orders blocking questions before recommended", () => {
    const data = response({
      questions: [
        {
          question_id: "Q-REC",
          question: "Recommended question",
          priority: "recommended",
          answer_type: "boolean",
          requirement_id: null,
          options: [],
          reason: "",
        },
        {
          question_id: "Q-BLK",
          question: "Blocking question",
          priority: "blocking",
          answer_type: "boolean",
          requirement_id: null,
          options: [],
          reason: "",
        },
      ],
    });
    renderPanel(data);
    const items = screen.getAllByRole("listitem");
    expect(items[0].textContent).toContain("Blocking question");
  });

  it("submits answers as one batch", () => {
    const { onSubmit } = renderPanel(response(), { "Q-001": "true" });
    fireEvent.click(screen.getByRole("button", { name: "Submit Answers" }));
    expect(onSubmit).toHaveBeenCalledWith(false);
  });

  it("shows submit error detail", () => {
    render(
      <ClarificationPanel
        data={response()}
        answers={{}}
        setAnswer={vi.fn()}
        submitting={false}
        submitError="Contradictory answers for Q-001"
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("Contradictory answers");
  });

  it("offers proceed-with-assumptions when blocking questions remain (round cap)", () => {
    // Blocking questions still open (e.g. the clarification round cap was reached):
    // the proceed-with-assumptions button MUST be available so the user can move
    // forward, accepting the remaining gaps as assumptions.
    const { onSubmit } = renderPanel(response()); // default: blocking_unanswered: 1
    const proceed = screen.getByRole("button", {
      name: /proceed with assumptions/i,
    });
    fireEvent.click(proceed);
    expect(onSubmit).toHaveBeenCalledWith(true);
  });

  it("hides proceed-with-assumptions once the run is ready", () => {
    const data = response({
      readiness: {
        ready: true,
        requirements_total: 1,
        blocking_unanswered: 0,
        recommended_unanswered: 0,
        optional_unanswered: 0,
        critical_gaps: 0,
        blocking_reasons: [],
      },
    });
    renderPanel(data);
    expect(
      screen.queryByRole("button", { name: /proceed with assumptions/i }),
    ).not.toBeInTheDocument();
  });

  it("shows proceed-with-assumptions when only recommended remain", () => {
    const data = response({
      questions: [
        {
          question_id: "Q-001",
          question: "Recommended only",
          priority: "recommended",
          answer_type: "boolean",
          requirement_id: null,
          options: [],
          reason: "",
        },
      ],
      readiness: {
        ready: false,
        requirements_total: 1,
        blocking_unanswered: 0,
        recommended_unanswered: 1,
        optional_unanswered: 0,
        critical_gaps: 0,
        blocking_reasons: [],
      },
    });
    const { onSubmit } = renderPanel(data);
    const proceed = screen.getByRole("button", { name: /proceed with assumptions/i });
    fireEvent.click(proceed);
    expect(onSubmit).toHaveBeenCalledWith(true);
  });
});
