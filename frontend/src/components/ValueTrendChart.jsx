import {useEffect, useRef} from "react";
import * as d3 from "d3";

// History: [{date, value}] forecast: [{date, value, lower, upper}]
export default function valueTrendChart({history = [], forecast = []}) {
    const ref = useRef(null);

    useEffect(() => {
        const el = ref.current;
        if(!el) return;
        d3.select(el).selectAll("*").remove();
        if(history.length === 0) return;

        const width = el.clientWidth || 640;
        const height = 280;
        const margin = {top: 16, right: 16, bottom: 28, left: 56};

        const parse = (d) => (d instanceof Date ? d : new Date(d));
        const hist = history.map((d) => ({date: parse(d.date), value: d.value}));
        const fcast = forecast.map((d) => ({
            date: parse(d.date),
            value: d.value,
            lower: d.lower,
            upper: d.upper,
        }));

        const allDates = [...hist, ...fcast].map((d) => d.date);
        const allValues = [
            ...hist.map((d) => d.value),
            ...fcast.flatMap((d) => [d.value, d.lower, d.upper]),
        ].filter((v) => v != null);

        const x = d3
            .scaleTime()
            .domain(d3.extent(allDates))
            .range([margin.left, width - margin.right]);

        const y = d3
            .scaleLinear()
            .domain([d3.min(allValues) * 0.97, d3.max(allValues) * 1.03])
            .range([height - margin.bottom, margin.top]);

        const svg = d3
            .select(el)
            .append("svg")
            .attr("viewBox", `0 0 ${width} ${height}`)
            .attr("width", "100%")
            .attr("height", height);

        // gridlines
        svg
            .append("g")
            .attr("transform", `translate(0, ${height - margin.bottom}`)
            .call(
                d3
                    .axixBottom(x)
                    .ticks(6)
                    .tickSize(-(height - margin.top - margin.bottom))
            )
            .call((g) => g.select(".domain").remove())
            .call((g) =>
                g.selectAll("line").attr("stroke", "#DED4BC").attr("stroke-dasharray", "2,3")
            )
            .call((g) =>
                g
                    .selectAll("text")
                    .attr("fill", "#211E19")
                    .attr("fill-opacity", 0.45)
                    .attr("font-size", 11)
                    .attr("font-family", "IBM Plex Mono")
            );

        svg
            .append("g")
            .attr("transform", `translate(${margin.left}, 0)`)
            .call(
                d3
                    .axisLeft(y)
                    .ticks(5)
                    .tickFormat((v) => `$${d3.format(".2s")(v).replace("G", "B")}`)
            )
            .call((g) => g.select(".domain").remove())
            .call((g) => g.selectAll("line").remove())
            .call((g) =>
                g
                    .selectAll("text")
                    .attr("fill", "#211E19")
                    .attr("fill-opacity", 0.45)
                    .attr("font-size", 11)
                    .attr("font-family", "IBM Plex Mono")
            );

        // Forecast confidence band
        if(fcast.length) {
            const band = d3
                .area()
                .x((d) => x(d.date))
                .y0((d) => y(d.lower))
                .y1((d) => y(d.upper))
                .curve(d3.curveMonotoneX);

            svg
                .append("path")
                .datum(fcast)
                .attr("d", band)
                .attr("fill", "#2B5C63")
                .attr("fill-opacity", 0.12);
        }

        // Historical line (solid, terracotta)
        if(fcast.length) {
            const bridge = [hist[hist.length - 1], ...fcast].filter(Boolean);
            const fLine = d3
                .line()
                .x((d) => x(d.date))
                .y((d) => y(d.value))
                .curve(d3.curveMonotoneX);

            svg
                .append("path")
                .datum(bridge)
                .attr("d", fLine)
                .attr("fill", "none")
                .attr("stroke", "#2B5C63")
                .attr("stroke-width", 2.5)
                .attr("stroke-dasharray", "5,4");
        }

        // Dot on last known value
        const last = hist[hist.length - 1];
        if(last) {
            svg
                .append("circle")
                .attr("cx", x(last.date))
                .attr("cy", y(last.value))
                .attr("r", 4)
                .attr("fill", "#A6461F");
        }
    }, [history, forecast]);

    return <div ref={ref} className="w-full" />;
}